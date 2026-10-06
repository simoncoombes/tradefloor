"""Measure the cost of a large order sliced over time: a meta-order.

    python tools/calibration/metaorder_cost.py [--base pt-v20]
        [--set name=value[,name=value...] ...] [--seeds 4] [--names 40] [--warm 60]
        [--sizes 0.02,0.05,0.1,0.2] [--slices 1,6,18,36] [--out FILE]

Row C9 (`impact_curve.py`) reads ONE immediate sweep of the agent-facing
book. This reads an order of `Q = f * V` shares split into `n` equal
slices ten ticks apart, starting fifteen minutes after the open: one slice
is a block, 6 slices take an hour, 18 half a day, 36 a whole day. That is
the order the published meta-order studies measure.

Each order runs on a fork of one warmed engine beside a second fork that
does not trade, on the same draws, so every difference between the two is
the order's own. Per name, seed, size and schedule, in units of the name's
daily volatility `sigma` realised over the warm-up:

- ``cost``: the implementation shortfall, the filled-share-weighted average
  of each slice's price against the untraded fork's mid at that moment.
- ``peak``: the log price gap between the two forks one tick after the last
  slice, when its impact has reached the tape.
- ``decay``: the same gap at the day's close and after 1, 2 and 5 more
  sessions, as a share of the peak.

It fits ``cost = c * (Q/V)^b`` per schedule over the sizes given, and
prints the ratio of the day-long order's cost to the block's, which is one
for an order whose cost does not depend on how it is sliced.

The literature it is read against:

- Toth, Lemperiere, Deremble, de Lataillade, Kockelkoren and Bouchaud
  (Physical Review X 1, 021006, 2011) and Bacry, Iuga, Lasnier and Lehalle
  (Market Microstructure and Liquidity 1(2), 2015): the peak impact of a
  meta-order is ``Y sigma sqrt(Q/V)`` with ``Y`` of order one, close to
  independent of its duration within a day. Band for the peak, Y in
  [0.5, 1]; for the cost, two thirds of that, [0.33, 0.67].
- Moro, Vicente, Moyano, Gerig, Farmer, Vaglica, Lillo and Mantegna
  (Physical Review E 80, 066102, 2009) and Bershova and Rakhlin
  (Quantitative Finance 13(11), 2013): after the order ends the impact
  falls to about two thirds of its peak, then decays slowly.
- Almgren, Thum, Hauptmann and Li (Risk 18(7), 2005): over one day,
  ``0.142 sigma (Q/V)^0.6 + 0.157 sigma Q/V``, printed beside the result.

Local and light: about a minute at the defaults. Nothing is written unless
``--out`` is given.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import struct
import sys

import tradefloor as tf

GAP = 10
START = 15
DECAY_DAYS = (1, 2, 5)


def f64(buf: bytes) -> list[float]:
    return list(struct.unpack("<%dd" % (len(buf) // 8), buf))


def run(e, t: int, n: int, close: bool = False) -> int:
    """Run `n` ticks from `t` ticks after the 09:30 open, clock advancing."""
    if n > 0:
        m = 9 * 60 + 30 + t
        e.run_session(m // 60, m % 60, 3, n, close_at_end=close)
    elif close:
        e.close_market()
    return t + n


def full_day(e) -> None:
    e.open_market()
    e.run_session(9, 30, 3, 390, close_at_end=True)


def warm(seed: int, universe, params, days: int):
    e = tf.Engine(seed=seed, universe=universe, model=params)
    closes = [f64(e.prices())]
    for _ in range(days):
        full_day(e)
        closes.append(f64(e.prices()))
    sig = []
    for i in range(len(universe)):
        r = [math.log(closes[d + 1][i] / closes[d][i]) for d in range(days)]
        sig.append(statistics.pstdev(r))
    return e, sig


def one_order(base, i: int, tk: str, q: float, n: int, sigma: float) -> dict:
    e, ctl = base.fork(2)
    e.open_market()
    ctl.open_market()
    t = run(e, 0, START)
    run(ctl, 0, START)
    num = den = 0.0
    for k in range(n):
        mid = ctl.book(tk).mid_price
        r = e.submit("meta", tk, q / n)
        if r["filled"] > 0:
            num += r["filled"] * (r["average_price"] - mid)
            den += r["filled"] * mid
        if k < n - 1:
            run(e, t, GAP)
            t = run(ctl, t, GAP)
    run(e, t, 1)
    t = run(ctl, t, 1)

    def gap() -> float:
        return math.log(f64(e.prices())[i] / f64(ctl.prices())[i]) / sigma

    peak = gap()
    run(e, t, 390 - t, True)
    run(ctl, t, 390 - t, True)
    after = {0: gap()}
    done = 0
    for d in DECAY_DAYS:
        for _ in range(d - done):
            full_day(e)
            full_day(ctl)
        done = d
        after[d] = gap()
    return {"cost": num / den / sigma if den else float("nan"),
            "peak": peak, "after": after}


def fit(fs: list[float], ys: list[float]) -> tuple[float, float]:
    xs = [math.log(f) for f in fs]
    ls = [math.log(max(y, 1e-9)) for y in ys]
    mx, my = statistics.fmean(xs), statistics.fmean(ls)
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ls)) / sum((x - mx) ** 2 for x in xs)
    return b, math.exp(my - b * mx)


def almgren_day(f: float) -> float:
    return 0.142 * f ** 0.6 + 0.5 * 0.314 * f


def parse_set(items: list[str]) -> dict[str, float]:
    """`name=value` items, each possibly a comma-separated list of them."""
    out = {}
    for item in items:
        for kv in item.split(","):
            if kv.strip():
                k, v = kv.split("=", 1)
                out[k.strip()] = float(v)
    return out


def measure(base: str, over: dict[str, float], seeds: int, names: int,
            warm_days: int, sizes: list[float], slices: list[int],
            ranks: list[int]) -> dict:
    params = tf.ModelParams.from_preset(base, **over)
    u = tf.Universe.random(names, seed=111)
    mcap = [x.market_cap for x in u]
    order = sorted(range(len(u)), key=lambda k: -mcap[k])
    picks = [order[r] for r in ranks if r < len(order)]
    rows = []
    for s in range(seeds):
        e, sig = warm(311 + s, u, params, warm_days)
        for i in picks:
            tk = u[i].ticker
            for f in sizes:
                for n in slices:
                    r = one_order(e, i, tk, f * u[i].avg_volume, n, sig[i])
                    rows.append({"seed": 311 + s, "name": tk, "f": f, "n": n, **r})
    return {"base": base, "over": over, "rows": rows}


def summarise(result: dict, sizes: list[float], slices: list[int]) -> dict:
    rows = result["rows"]
    table: dict = {}
    for n in slices:
        per = {}
        for f in sizes:
            at = [r for r in rows if r["n"] == n and r["f"] == f]
            costs = [r["cost"] for r in at]
            peaks = [r["peak"] for r in at]
            pk = statistics.fmean(peaks)
            per[f] = {
                "cost": statistics.fmean(costs),
                "cost_se": (statistics.stdev(costs) / math.sqrt(len(costs))
                            if len(costs) > 1 else 0.0),
                "peak": pk,
                "after": {d: statistics.fmean(r["after"][d] for r in at) / pk
                          if pk else float("nan")
                          for d in (0, *DECAY_DAYS)},
            }
        b, c = fit(sizes, [per[f]["cost"] for f in sizes])
        bp, cp = fit(sizes, [per[f]["peak"] for f in sizes])
        table[n] = {"sizes": per, "fit_cost": (b, c), "fit_peak": (bp, cp)}
    n, f = max(slices), max(x for x in sizes if x <= 0.1 + 1e-12)
    names = sorted({r["name"] for r in rows}, key=lambda t: [r["name"] for r in rows].index(t))
    table["by_name"] = [(t, statistics.fmean(r["cost"] for r in rows
                                             if r["name"] == t and r["n"] == n and r["f"] == f))
                        for t in names]
    return table


def report(label: str, table: dict, sizes: list[float], slices: list[int]) -> None:
    print(f"== {label}")
    print("  slices  " + "  ".join(f"cost@{f:<5g}" for f in sizes)
          + "   fit cost c*(Q/V)^b   peak/(s sqrt f)@0.1   after close/1d/2d/5d (share of peak, f=0.1)")
    for n in slices:
        t = table[n]
        b, c = t["fit_cost"]
        f10 = t["sizes"].get(0.1) or t["sizes"][sizes[-1]]
        fk = 0.1 if 0.1 in t["sizes"] else sizes[-1]
        after = "/".join(f"{f10['after'][d]:.2f}" for d in (0, *DECAY_DAYS))
        print(f"  {n:6d}  " + "  ".join(f"{t['sizes'][f]['cost']:10.3f}" for f in sizes)
              + f"   {c:.3f}*(Q/V)^{b:.3f}      {f10['peak'] / math.sqrt(fk):.3f}"
              + f"             {after}")
    if 1 in table and max(slices) in table:
        ratio = "  ".join(f"{f:g}: {table[max(slices)]['sizes'][f]['cost'] / table[1]['sizes'][f]['cost']:.2f}"
                          for f in sizes)
        print(f"  day-long cost over block cost: {ratio}")
    print("  day-long cost per name at the largest size of 0.1 or less: " + "  ".join(
        f"{name}: {cost:.3f}" for name, cost in table["by_name"]))
    print("  standard error of the day-long cost: " + "  ".join(
        f"{f:g}: {table[max(slices)]['sizes'][f]['cost_se']:.3f}" for f in sizes))
    print("  Almgren et al. (2005) over one day: " + "  ".join(
        f"{f:g}: {almgren_day(f):.3f}" for f in sizes))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", default="pt-v20")
    ap.add_argument("--set", action="append", default=[],
                    help="ModelParams overrides, name=value or a comma-separated "
                         "list of them; repeatable")
    ap.add_argument("--compare", action="store_true",
                    help="also measure the base preset without the overrides")
    ap.add_argument("--seeds", type=int, default=4)
    ap.add_argument("--names", type=int, default=40)
    ap.add_argument("--ranks", default="5,15,30",
                    help="market-cap ranks of the names that trade")
    ap.add_argument("--warm", type=int, default=60)
    ap.add_argument("--sizes", default="0.02,0.05,0.1,0.2")
    ap.add_argument("--slices", default="1,6,18,36")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    sizes = [float(x) for x in args.sizes.split(",")]
    slices = [int(x) for x in args.slices.split(",")]
    ranks = [int(x) for x in args.ranks.split(",")]
    if max(slices) * GAP + START >= 389:
        ap.error("the schedule must end inside the session")
    over = parse_set(args.set)
    arms = [("off", {})] if args.compare or not over else []
    if over:
        arms.append(("on", over))
    out = {}
    for label, o in arms:
        res = measure(args.base, o, args.seeds, args.names, args.warm, sizes,
                      slices, ranks)
        table = summarise(res, sizes, slices)
        report(f"{label}: {args.base} {o or ''}", table, sizes, slices)
        out[label] = {"over": o, "table": {str(k): v for k, v in table.items()},
                      "rows": res["rows"]}
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump({"base": args.base, "seeds": args.seeds, "names": args.names,
                       "ranks": ranks, "warm": args.warm, "arms": out}, fh,
                      indent=1, sort_keys=True, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
