"""The audit's rate-index timing probe (audit085 regression probe_bond_timing,
as the bond-timing desk ran it: r13/bt-desk/probe_bond_timing3.py) for every
arm in one pool. An agent in tf.evaluate (six steps a day, the engine's own
book and costs, max_leverage 2) reads the equity index's move from the open
to step K and goes long a rate index if equities rose, short if they fell
(1x net worth), unwinding at the next day's first step. MODE long: always
long into the close. Universe.random(40, seed=seed, bonds=True).
usage: probe_all.py OUT.json ARMS_FILE SEEDS DAYS WORKERS"""
import sys, json
from concurrent.futures import ProcessPoolExecutor
import numpy as np

CONFIGS = [("IGCORP", 5, "sign"), ("IGCORP", 4, "sign"), ("IGCORP", 5, "long"),
           ("UST10Y", 5, "sign"), ("UST10Y", 4, "sign")]


def one(spec):
    name, dials, seed, days, ticker, K, mode = spec
    import tradefloor as tf
    RATE = {x["ticker"] for x in tf.rate_specs()}

    class BondTimer:
        def __init__(self):
            self.open_ix = None

        def act(self, obs):
            px = np.array(obs.prices)
            eq = [i for i, t in enumerate(obs.tickers) if t not in RATE]
            bi = obs.tickers.index(ticker)
            k = obs.step_of_day
            if k == 0:
                self.open_ix = np.exp(np.log(px[eq]).mean())
                q = -obs.position(ticker)
                return {ticker: float(round(q))} if abs(q) >= 1 else {}
            if k == K and self.open_ix:
                r = np.exp(np.log(px[eq]).mean()) / self.open_ix - 1
                if mode == "long":
                    r = 1.0
                nw = obs.portfolio.net_worth(obs.engine)
                want = np.sign(r) * nw / px[bi]
                q = want - obs.position(ticker)
                return {ticker: float(round(q))} if abs(q) >= 1 else {}
            return {}

    class Nothing:
        def act(self, obs):
            return {}

    u = tf.Universe.random(40, seed=seed, bonds=True)
    model = tf.ModelParams.from_preset("pt-v20", **dials)
    sc = tf.evaluate({"cash": Nothing(), "timer": BondTimer()}, seed=seed, universe=u, days=days,
                     max_leverage=2.0, model=model)
    t = sc["timer"]
    ann = ((1 + t.return_pct / 100) ** (252 / days) - 1) * 100
    return name, f"{ticker}-{K}-{mode}", seed, ann, t.trades, len(t.errors)


def main():
    out, arms_file, seeds, days, workers = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5])
    sys.path.insert(0, __file__.rsplit("/", 1)[0])
    from r13gen import arms_of, seeds_of
    specs = [(n, d, s, days, tk, K, mode) for n, d in arms_of(arms_file) for (tk, K, mode) in CONFIGS
             for s in seeds_of(seeds)]
    res = {}
    with ProcessPoolExecutor(workers) as ex:
        for name, cfg, seed, ann, tr, ne in ex.map(one, specs):
            res.setdefault(name, {}).setdefault(cfg, {})[str(seed)] = {"ann": ann, "trades": tr, "errors": ne}
    json.dump({"kind": "probe_all", "days": days, "seeds": seeds, "per_arm": res}, open(out, "w"), indent=1)
    for name, cfgs in res.items():
        for cfg, rows in cfgs.items():
            x = np.array([r["ann"] for r in rows.values()])
            print(f"{name:8s} {cfg:18s} mean {x.mean():+6.1f}%/yr  positive {int((x > 0).sum())}/{len(x)}", flush=True)


if __name__ == "__main__":
    main()
