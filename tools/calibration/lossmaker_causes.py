"""Which cause tops a name's move in a rate-shock market, with and without
`lossmaker_rate_sensitivity`.

The benchmark asks an agent why a name moved over a window. This measures,
on the packaged `rate_shock` (+200 bp on the corporate yield and the policy
rate from day 50, held), how often the economy's repricing of value is the
top cause, for loss-makers and for profitable companies apart, with the dial
off and on, on the same seeds.

The causes are netted the way tradefloor-serve's `netted_causes` nets them
(app/insights/facts.py, read 2026-10-11), from what the engine exposes:

- the twelve `Engine.FACTORS` (`Engine.attribution`, per day), with
  `fair_value_shift` netted into `fair_value_company`;
- the rest of the day's log move, `fair_value_and_book`, split four ways:
  `fair_value_market`, the capitalisation-weighted mean over every name of
  the day's log change in fair value per share (the snapshot's
  `tick_fundamental`, weights the market caps before the open);
  `fair_value_company`, the name's own change less that mean; `repricing`,
  the day's change in the log gap from the last print to the price the
  close left; and `order_book`, what remains.

The engine has no `fair_value_market` or `fair_value_company` of its own:
serve derives both from `tick_fundamental` as above. So a loss-maker's rate
term reaches `fair_value_market` only through the capitalisation-weighted
mean, which loss-makers barely move, and the rest of it lands in
`fair_value_company`.

Per name and window, each cause is summed over the window's days (signed)
and the cause with the largest absolute sum tops it. Windows of 1, 5 and 20
days: the three that start on the shock's day, and every window tiled over
days 50-109. Reported: the share of name-windows topped by
`fair_value_market` or `repricing`, by their sum taken as one cause, and by
`fair_value_company`.

    python lossmaker_causes.py OUT.json --seeds 270301-270330 --k 11 --workers 6
"""
from __future__ import annotations

import argparse
import json
import math
import struct
from collections import Counter
from concurrent.futures import ProcessPoolExecutor

DAYS = 110
SHOCK = 50
WINDOWS = (1, 5, 20)


def floats(raw: bytes) -> list[float]:
    return list(struct.unpack("<%dd" % (len(raw) // 8), raw))


def run(spec):
    preset, k, seed, names = spec
    import tradefloor as tf
    u = list(tf.Universe.random(names, seed=seed))
    dials = {"lossmaker_rate_sensitivity": k} if k else {}
    e = tf.Engine(seed=seed, universe=u, model=tf.ModelParams.from_preset(preset, **dials))
    sc = tf.Scenario.load("rate_shock")
    n = len(u)
    loss = [inst.eps <= 0 for inst in u]
    fair_prev = floats(e.state_snapshot()["tick_fundamental"])
    gap_prev = [0.0] * n
    days = []
    for d in range(DAYS):
        price_prev = floats(e.prices())
        sc.apply(e, d)
        caps = floats(e.column("market_cap"))
        e.open_market()
        e.run_session(9, 30, 3, 390)
        e.close_market()
        close = floats(e.prices())
        attr = {f: floats(e.attribution(f)) for f in tf.Engine.FACTORS}
        fair = floats(e.state_snapshot()["tick_fundamental"])
        last = floats(e.session_prices())[-n:]
        gap = [math.log(p / q) for p, q in zip(close, last)]
        ok = [a > 0 and b > 0 and math.isfinite(a) and math.isfinite(b)
              for a, b in zip(fair_prev, fair)]
        fc = [math.log(b / a) if good else 0.0 for a, b, good in zip(fair_prev, fair, ok)]
        w = sum(c for c, good in zip(caps, ok) if good)
        # Day 0 has no fair value before it: nothing to split, as serve's
        # `_market_fair_change` returns nothing there.
        market = sum(c * x for c, x, good in zip(caps, fc, ok) if good) / w if w > 0 else 0.0
        rows = []
        for j in range(n):
            move = math.log(close[j] / price_prev[j])
            causes = {f: attr[f][j] for f in tf.Engine.FACTORS}
            rest = move - math.fsum(causes.values())
            shift = causes.pop("fair_value_shift")
            rep = gap[j] - gap_prev[j]
            causes["fair_value_market"] = market
            causes["fair_value_company"] = fc[j] - market + shift
            causes["repricing"] = rep
            causes["order_book"] = rest - fc[j] - rep
            rows.append(causes)
        days.append(rows)
        fair_prev, gap_prev = fair, gap
    return seed, k, loss, days


def top(summed: dict[str, float], merge: bool) -> str:
    c = dict(summed)
    if merge:
        c["market_and_repricing"] = c.pop("fair_value_market") + c.pop("repricing")
    return max(c, key=lambda key: (abs(c[key]), key))


def windows(length: int):
    anchored = [(SHOCK, SHOCK + length)]
    tiled = [(a, a + length) for a in range(SHOCK, DAYS - length + 1, length)]
    return anchored, tiled


def tally(results, k):
    out = {}
    for length in WINDOWS:
        anchored, tiled = windows(length)
        for label, spans in (("anchored", anchored), ("tiled", tiled)):
            for group in ("loss", "profitable"):
                counts, merged, n = Counter(), Counter(), 0
                for seed, kk, loss, days in results:
                    if kk != k:
                        continue
                    for j, is_loss in enumerate(loss):
                        if is_loss != (group == "loss"):
                            continue
                        for a, b in spans:
                            s: dict[str, float] = {}
                            for d in range(a, b):
                                for key, v in days[d][j].items():
                                    s[key] = s.get(key, 0.0) + v
                            counts[top(s, False)] += 1
                            merged[top(s, True)] += 1
                            n += 1
                if n == 0:
                    continue
                either = counts["fair_value_market"] + counts["repricing"]
                out[f"{length}d/{label}/{group}"] = {
                    "name_windows": n,
                    "market_or_repricing": either / n,
                    "market_plus_repricing_as_one": merged["market_and_repricing"] / n,
                    "fair_value_company": counts["fair_value_company"] / n,
                    "top_causes": dict(counts.most_common(5)),
                }
    return out


def seeds_of(text: str) -> list[int]:
    out = []
    for part in text.split(","):
        lo, _, hi = part.partition("-")
        out += list(range(int(lo), int(hi or lo) + 1))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out")
    ap.add_argument("--preset", default="pt-v21")
    ap.add_argument("--seeds", required=True, help="market seeds; each also seeds its roster")
    ap.add_argument("--names", type=int, default=20)
    ap.add_argument("--k", type=float, default=11.0, help="the dial's value for the on arm")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    specs = [(a.preset, k, s, a.names) for s in seeds_of(a.seeds) for k in (0.0, a.k)]
    with ProcessPoolExecutor(a.workers) as pool:
        results = list(pool.map(run, specs))
    report = {"preset": a.preset, "seeds": a.seeds, "names": a.names, "k": a.k,
              "loss_makers": sum(sum(r[2]) for r in results if r[1] == 0.0),
              "off": tally(results, 0.0), "on": tally(results, a.k)}
    json.dump(report, open(a.out, "w"), indent=1)
    for key in report["off"]:
        f, n = report["off"][key], report["on"][key]
        print(f"{key:28s} n={f['name_windows']:5d}  market|repricing {f['market_or_repricing']:.3f} -> "
              f"{n['market_or_repricing']:.3f}   as one {f['market_plus_repricing_as_one']:.3f} -> "
              f"{n['market_plus_repricing_as_one']:.3f}   company {f['fair_value_company']:.3f} -> "
              f"{n['fair_value_company']:.3f}")


if __name__ == "__main__":
    main()
