"""A tighter real-side CIV elasticity, and an honest error bar for it.

Round 86 could not tell the model from reality on this statistic: real read
+0.283 with a standard error of 0.104 over ten NON-OVERLAPPING annual windows,
a 95% interval of +0.079 to +0.487, and every preset sat inside it. Ten points
is all a decade of non-overlapping years holds, so the limit was the estimator
rather than the data.

This overlaps the windows, stepping monthly rather than annually, which turns
ten points into about a hundred and twenty. That buys nothing on its own: the
windows share 92% of their observations, so the naive standard error across
them is far too small and would manufacture the very precision the exercise is
meant to establish. The error bar therefore comes from a MOVING BLOCK
BOOTSTRAP whose block length is the overlap horizon itself, twelve windows, so
resampling moves whole independent stretches rather than shuffling points that
are nearly copies of each other.

Reported alongside the naive standard error, deliberately. The gap between the
two is the size of the mistake that overlapping windows invite.
"""
import json, math, random, statistics as st, sys, os

SP = "/private/tmp/claude-503/-Users-simoncoombes-nw-Dev/76cab463-16f4-4a89-baac-68bc86680c4c/scratchpad"
CACHE = f"{SP}/civ/real_closes.json"
WINDOW, STEP = 252, 21

def load():
    data = json.load(open(CACHE))
    tick = sorted(data)
    common = None
    for t in tick:
        s = {ts for ts, a in zip(data[t]["ts"], data[t]["adj"]) if a is not None}
        common = s if common is None else (common & s)
    common = sorted(common)
    idx = {t: dict(zip(data[t]["ts"], data[t]["adj"])) for t in tick}
    closes = {t: [idx[t][ts] for ts in common] for t in tick}
    rets = {t: [math.log(closes[t][i]/closes[t][i-1]) for i in range(1, len(common))]
            for t in tick}
    return tick, rets, len(common) - 1

def window_stats(rets, tick, a, b):
    mat = [rets[t][a:b] for t in tick]
    T, N = b - a, len(mat)
    mkt = [sum(mat[k][t] for k in range(N))/N for t in range(T)]
    mm = sum(mkt)/T
    v_common = sum((x-mm)**2 for x in mkt)/T
    resids = []
    for row in mat:
        mu = sum(row)/T
        v_tot = sum((x-mu)**2 for x in row)/T
        cov = sum((row[t]-mu)*(mkt[t]-mm) for t in range(T))/T
        beta = cov/v_common
        resids.append(v_tot - beta*beta*v_common)
    resids.sort()
    return v_common, resids[N//2]

def slope(pts):
    xs = [math.log(x) for x, _ in pts]; ys = [math.log(y) for _, y in pts]
    n = len(xs); mx = sum(xs)/n; my = sum(ys)/n
    den = sum((x-mx)**2 for x in xs)
    return sum((x-mx)*(y-my) for x, y in zip(xs, ys))/den

if __name__ == "__main__":
    tick, rets, R = load()
    print(f"{len(tick)} names, {R} daily returns\n")

    non = [window_stats(rets, tick, i*WINDOW, (i+1)*WINDOW) for i in range(R//WINDOW)]
    b_non = slope(non)
    xs = [math.log(x) for x, _ in non]; ys = [math.log(y) for _, y in non]
    a_ = sum(ys)/len(ys) - b_non*sum(xs)/len(xs)
    r = [y-(a_+b_non*x) for x, y in zip(xs, ys)]
    mx = sum(xs)/len(xs)
    se_non = math.sqrt(sum(e*e for e in r)/(len(xs)-2)/sum((x-mx)**2 for x in xs))
    print(f"NON-OVERLAPPING, as round 86 measured it")
    print(f"  n={len(non):3d}  elasticity {b_non:+.3f}  se {se_non:.3f}  "
          f"95% [{b_non-1.96*se_non:+.3f}, {b_non+1.96*se_non:+.3f}]\n")

    ov = [window_stats(rets, tick, s, s+WINDOW)
          for s in range(0, R-WINDOW+1, STEP)]
    b_ov = slope(ov)
    xs = [math.log(x) for x, _ in ov]; ys = [math.log(y) for _, y in ov]
    a_ = sum(ys)/len(ys) - b_ov*sum(xs)/len(xs)
    r = [y-(a_+b_ov*x) for x, y in zip(xs, ys)]
    mx = sum(xs)/len(xs)
    se_naive = math.sqrt(sum(e*e for e in r)/(len(xs)-2)/sum((x-mx)**2 for x in xs))

    L = WINDOW//STEP                     # windows sharing data: the overlap horizon
    rng = random.Random(20260828)
    boots = []
    nblocks = max(1, len(ov)//L)
    for _ in range(2000):
        samp = []
        for _ in range(nblocks):
            s = rng.randrange(0, len(ov)-L+1)
            samp.extend(ov[s:s+L])
        try: boots.append(slope(samp))
        except ZeroDivisionError: pass
    boots.sort()
    lo, hi = boots[int(.025*len(boots))], boots[int(.975*len(boots))]
    se_boot = st.pstdev(boots)
    print(f"OVERLAPPING, stepped {STEP} days")
    print(f"  n={len(ov):3d}  elasticity {b_ov:+.3f}")
    print(f"  naive se     {se_naive:.3f}   95% [{b_ov-1.96*se_naive:+.3f}, {b_ov+1.96*se_naive:+.3f}]   <- WRONG, windows share 92% of their data")
    print(f"  block boot   {se_boot:.3f}   95% [{lo:+.3f}, {hi:+.3f}]   <- block length {L} windows, {len(boots)} resamples")
    print()
    print("model side, round 86, twenty-four seeds each:")
    for name, v in (("pt-v14", 0.173), ("d37ship", 0.186), ("d32ship", 0.198)):
        verdict = "INSIDE" if lo <= v <= hi else "OUTSIDE -> a real gap"
        print(f"  {name:8s} {v:+.3f}   {verdict}")
