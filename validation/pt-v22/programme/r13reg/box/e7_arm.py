"""E7. The proposed rows on the ENGINE implementation (branch worktree
tf-wt-real-arrival-order, book_arrival_shuffle in ModelParams, uncommitted on
5b56d0b), pt-v20 with the dial at 0.0 (label) and 1.0 (shuffle).
VARIANT (r14 screen): argv START ARM ROW OUTDIR [DIALS]; the arm's own dials (k=v,...) on pt-v20, so
the rows read the arm as registered, not the dial alone. ROW in q1 q2 q3 q4. 30 seeds from START.
q1: two identical 10%-ADV market buys of AAC at day 3 step 1 (4-day run).
q2: Momentum(6,5,1.0,0.05) x2, 5e7 each, 20 days.
q3: same x4 labelled m0..m3, 10 days; Spearman(label rank, cost).
q4: two identical buy Limits at the best bid, a quarter of ADV each, day 1 step 1, 2-day run.
Owner decision 10 (2026-09-28): q2 also records each seed's a-first share (`afirst`: the share of the run's steps
whose arrival order put a first, read from the trace) and q3 each seed's Spearman(label rank, mean arrival position)
(`lpos`, ranks averaged over ties), the arrival luck the fourteenth registration's AO3 and AO4 regress out (grade_all
.ao_luck). Both are None when the trace carries no arrival order (the switch off). AO_PROTOCOL (default ao) names the
seed protocol the rule checks: aopool for the pooled 400-seed set (AOPOOL_FIRST), run in blocks of AO_N seeds, each
block a part of the set (so checked with exact=False; grade_all checks the union is exactly the set).
"""
import json, statistics as S, sys, time
import tradefloor as tf
from tradefloor.baselines import Momentum
from tradefloor.counterfactual import World

U = tf.Universe.random(20, seed=111)
START, MODE, ROW, OUTDIR = int(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
DIALS = {k: float(v) for k, v in (x.split("=") for x in (sys.argv[5] if len(sys.argv) > 5 else "").split(",") if x)}
SEEDS = range(START, START + int(__import__("os").environ.get("AO_N", "30")))
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import seedplan  # noqa: E402  the registered-grade rule: held out, or exactly the registered set (AO_FIRST_SEED)
PROTO = __import__("os").environ.get("AO_PROTOCOL", "ao")
seedplan.guard(SEEDS, PROTO, exact=PROTO != "aopool")
M = tf.ModelParams.from_preset("pt-v20", **DIALS)
mk = lambda: Momentum(lookback=6, top_k=5, gross=1.0, max_participation=0.05)


def order_at(w, i, labels):
    r = w.trace[i]
    return r.get("arrival", sorted(labels))


class Buyer:
    AT = 6 * 3 + 1
    def act(self, obs):
        return {"AAC": 0.10 * obs.avg_volume("AAC")} if obs.step == self.AT else {}


class Rester:
    AT = 6 * 1 + 1
    def act(self, obs):
        if obs.step != self.AT:
            return {}
        return {"AAC": tf.Limit(0.25 * obs.avg_volume("AAC"), obs.engine.book("AAC").best_bid)}


def vwap(w, l):
    f = w.portfolios[l].fills
    return sum(x["price"] * abs(x["quantity"]) for x in f) / sum(abs(x["quantity"]) for x in f)


def filled(w, l):
    return sum(abs(f["quantity"]) for f in w.portfolios[l].fills)


def spearman(x, y):
    def rank(v):
        o = sorted(range(len(v)), key=lambda i: v[i]); r = [0] * len(v)
        for k, i in enumerate(o): r[i] = k
        return r
    rx, ry = rank(x), rank(y)
    mx, my = S.mean(rx), S.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** .5
    return num / den if den else 0.0


def spearman_avg(x, y):
    """Spearman's rho with tied values given their mean rank (scipy.stats.spearmanr's convention): the mean arrival
    positions of four labels over a short run tie often, and breaking ties by index would lean on label order."""
    def rank(v):
        o = sorted(range(len(v)), key=lambda i: v[i]); r = [0.0] * len(v); k = 0
        while k < len(o):
            j = k
            while j + 1 < len(o) and v[o[j + 1]] == v[o[k]]:
                j += 1
            for m in range(k, j + 1):
                r[o[m]] = (k + j) / 2
            k = j + 1
        return r
    rx, ry = rank(x), rank(y)
    mx, my = S.mean(rx), S.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** .5
    return num / den if den else 0.0


t0 = time.process_time()
out = {"start": START, "n": len(SEEDS), "mode": MODE, "row": ROW, "fingerprint": M.fingerprint}
if ROW == "q1":
    gap, prem, first_b = [], [], 0
    for seed in SEEDS:
        w = World(seed=seed, universe=U, model=M, agents={"a": Buyer(), "b": Buyer()},
                  max_leverage=None, cash=1e12)
        w.run(days=4)
        va, vb = vwap(w, "a"), vwap(w, "b")
        first = order_at(w, Buyer.AT, "ab")[0]
        first_b += first == "b"
        gap.append((vb / va - 1) * 1e4)
        prem.append(((va / vb) if first == "b" else (vb / va)) * 1e4 - 1e4)
    out.update(gap=gap, prem=prem, b_first=first_b)
elif ROW == "q2":
    ret, cost, afirst = [], [], []
    for seed in SEEDS:
        w = World(seed=seed, universe=U, model=M, cash=5e7, agents={"a": mk(), "b": mk()})
        w.run(days=20)
        ret.append((w.net_worth(agent="a") - w.net_worth(agent="b")) / 5e7 * 1e4)
        cost.append(w.summary(agent="b")["execution_cost_bps"] - w.summary(agent="a")["execution_cost_bps"])
        arr = [r.get("arrival") for r in w.trace]
        afirst.append(None if not arr or None in arr else sum(o[0] == "a" for o in arr) / len(arr))
    out.update(ret=ret, cost=cost, afirst=afirst)
elif ROW == "q3":
    labels = [f"m{i}" for i in range(4)]
    rho, spread, posrho, lpos = [], [], [], []
    for seed in SEEDS:
        w = World(seed=seed, universe=U, model=M, cash=5e7, agents={l: mk() for l in labels})
        w.run(days=10)
        costs = [w.summary(agent=l)["execution_cost_bps"] for l in labels]
        rets = [w.net_worth(agent=l) / 5e7 - 1 for l in labels]
        rho.append(spearman(list(range(4)), costs))
        spread.append((max(rets) - min(rets)) * 100)
        pos = [S.mean(order_at(w, i, labels).index(l) for i in range(len(w.trace))) for l in labels]
        posrho.append(spearman(pos, costs))
        arr = [r.get("arrival") for r in w.trace]
        lpos.append(None if not arr or None in arr else spearman_avg(list(range(4)), pos))
    out.update(rho=rho, spread=spread, posrho=posrho, lpos=lpos)
elif ROW == "q4":
    share_a, share_first = [], []
    for seed in SEEDS:
        w = World(seed=seed, universe=U, model=M, agents={"a": Rester(), "b": Rester()},
                  max_leverage=None, cash=1e12)
        w.run(days=2)
        fa, fb = filled(w, "a"), filled(w, "b")
        first = order_at(w, Rester.AT, "ab")[0]
        if fa + fb > 0 and min(fa, fb) < 0.999 * max(fa, fb):
            share_a.append(fa / (fa + fb))
            share_first.append((fa if first == "a" else fb) / (fa + fb))
    out.update(share_a=share_a, share_first=share_first)
out["cpu"] = round(time.process_time() - t0, 1)
out["dials"] = DIALS
json.dump(out, open(f"{OUTDIR}/{START}_{MODE}_{ROW}.json", "w"), indent=1)
print("done", START, MODE, ROW, out["cpu"], flush=True)
