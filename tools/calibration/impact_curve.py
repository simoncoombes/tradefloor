"""Measure the cost of size in the agent-facing book, against the square-root law.

    python tools/calibration/impact_curve.py [--seeds 3] [--names 40]
        [--days 60] [--coefficient 0.75] [--exponent 0.5] [--out FILE]

What it measures, on `Universe.random(names, seed=111 + k)` for each seed k,
after `days` untraded sessions (so each name's realised daily volatility can
be read off its own closes):

- **The curve.** For every name and both sides, the cost of an immediate
  order of `Q = f * V` shares against the book an agent meets
  (`Engine.book`, a read: nothing is traded), for f from 0.1% to 100% of
  daily volume. Two numbers per order, in bp of the arrival mid: the
  AVERAGE price paid (implementation shortfall) and the WORST price
  reached (the marginal, which is what the literature's peak impact is).
  Each is divided by `sigma * sqrt(f)`, sigma the name's realised daily
  volatility, which gives the effective coefficient the literature quotes.
- **The volatility slope.** At each f, the cross-sectional regression of
  cost on sigma: a law in sigma * sqrt(Q/V) has cost proportional to
  sigma, so cost / sigma is flat in sigma and the slope of log cost on
  log sigma is near one.
- **Refill.** With `book_shared` on, one order of `f` of daily volume, then
  the cost of an identical order k ticks later: how much of the first
  order's temporary impact the second still pays.

The literature it is compared with, stated as the script prints it:

- Toth, Lemperiere, Deremble, de Lataillade, Kockelkoren and Bouchaud
  (Physical Review X 1, 021006, 2011): the peak impact of a metaorder is
  `Y sigma sqrt(Q/V)` with Y of order one. The band used here is the one
  the brief states, Y in [0.5, 1]. Under a latent book that grows linearly
  with distance, the average cost of reaching that peak is two thirds of
  it, [0.33, 0.67].
- Almgren, Thum, Hauptmann and Li (Risk 18(7), 2005), US equity program
  trades: temporary cost `0.142 sigma (X / (V T))^0.6` and permanent
  impact `0.314 sigma X / V`, of which the trader pays half. Evaluated
  here at one step of six a day, T = 1/6.
- Frazzini, Israel and Moskowitz ("Trading Costs", 2018, AQR's own
  executions 1998-2016) find impact concave in size and well below earlier
  academic estimates for patient institutional execution; no coefficient
  is taken from them here.

Everything runs locally in about a minute at the defaults. Nothing is
written unless `--out` is given.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import struct
import sys

import tradefloor as tf

SIZES = (0.001, 0.003, 0.01, 0.03, 0.1, 0.3, 1.0)
REFILL_TICKS = (0, 1, 5, 15, 30, 65, 130)


def f64(buf: bytes) -> list[float]:
    return list(struct.unpack("<%dd" % (len(buf) // 8), buf))


BASE = "pt-v19"


def model(coefficient: float, exponent: float, shared: bool = False,
          half_life: float = 27.0, base: str | None = None,
          gamma: float = 0.0) -> tf.ModelParams:
    base = base or BASE
    over: dict[str, float] = {}
    if coefficient:
        over.update(book_depth_coefficient=coefficient,
                    book_depth_exponent=exponent, book_depth_reach=1.0)
    if shared:
        over["book_shared"] = 1.0
        if coefficient:
            over["book_refill_half_life"] = half_life
    if gamma:
        over["fill_impact_coefficient"] = gamma
    return tf.ModelParams.from_preset(base, **over)


def warm(seed: int, universe, params, days: int, vix: float | None = None):
    """An engine after `days` untraded sessions, and each name's realised
    daily volatility over them (log close to close). With `vix`, the fear
    gauge is pinned there before every session, which is how the regime
    comparison holds volatility high or low."""
    e = tf.Engine(seed=seed, universe=universe, model=params)
    closes = [f64(e.prices())]
    for _ in range(days):
        if vix is not None:
            e.pin_macro(vix=vix)
        e.open_market()
        e.run_session(9, 30, 3, 390)
        e.close_market()
        closes.append(f64(e.prices()))
    sig = []
    for i in range(len(universe)):
        r = [math.log(closes[d + 1][i] / closes[d][i]) for d in range(days)]
        sig.append(statistics.pstdev(r))
    e.open_market()
    e.run_session(9, 30, 3, 65)
    return e, sig


def cost_row(e, i: int, ticker: str, adv: float, sigma: float) -> list[dict]:
    book = e.book(ticker)
    mid = book.mid_price
    rows = []
    for f in SIZES:
        q = max(1.0, round(f * adv))
        for side in ("buy", "sell"):
            c = book.sweep_cost(side, q)
            if c is None or c.filled <= 0:
                continue
            sign = 1.0 if side == "buy" else -1.0
            avg = sign * (c.average_price / mid - 1.0) * 1e4
            worst = sign * (c.worst_price / mid - 1.0) * 1e4
            scale = sigma * math.sqrt(f) * 1e4
            rows.append({"name": i, "f": f, "side": side, "sigma": sigma,
                         "filled_share": c.filled / q, "avg_bp": avg,
                         "worst_bp": worst, "avg_Y": avg / scale,
                         "worst_Y": worst / scale})
    return rows


def refill(seed: int, universe, coefficient: float, exponent: float,
           f: float, half_life: float, gamma: float = 0.0
           ) -> dict[str, dict[int, float]]:
    """What a buy of `f` of daily volume leaves behind, k ticks later.

    Two forks of one state, one with the buy and one without, run the same
    k ticks on the same draws. Two numbers, median across names:

    - ``temporary``: the extra an identical second buy pays over its own
      book's mid, against the same buy in the fork with no first order. The
      depth the first took and has not refilled, and the maker's thinner
      ask while it is short. Measured against each fork's own mid, because
      the print the maker quotes around differs between the forks by the
      tape's own noise, tens of basis points, whatever the first order did.
    - ``permanent``: the difference in the name's `s`, in bp: the impact the
      model's price process carries (`fill_impact_coefficient`, or the
      imbalance law at 0.0).
    """
    temp: dict[int, list[float]] = {k: [] for k in REFILL_TICKS}
    perm: dict[int, list[float]] = {k: [] for k in REFILL_TICKS}
    params = model(coefficient, exponent, shared=True, half_life=half_life,
                   gamma=gamma)
    base, _ = warm(seed, universe, params, 1)
    for i, t in enumerate(base.tickers):
        q = max(1.0, round(f * universe[i].avg_volume))
        for k in REFILL_TICKS:
            costs, s = [], []
            untouched = base.book(t).mid_price
            for trade in (True, False):
                (e,) = base.fork(1)
                if trade:
                    e.submit("a", t, q)
                if k:
                    e.run_session(10, 35, 3, k)
                book = e.book(t)
                second = book.sweep_cost("buy", q)
                # At k = 0 no tick has run, so both forks share the mid the
                # first order met, and the consumed book's own mid (lifted
                # by the ask it lost) would hide half the cost.
                mid = untouched if k == 0 else book.mid_price
                costs.append(None if second is None
                             else (second.average_price / mid - 1.0) * 1e4)
                s.append(f64(e.column("mispricing_s"))[i])
            if None not in costs:
                temp[k].append(costs[0] - costs[1])
            perm[k].append((s[0] - s[1]) * 1e4)
    return {"temporary": {k: statistics.median(v) for k, v in temp.items() if v},
            "permanent": {k: statistics.median(v) for k, v in perm.items() if v}}


def almgren_cost(f: float, steps_per_day: int = 6) -> float:
    """Almgren et al. (2005) cost in units of sigma for X/V = f executed
    over one step: temporary 0.142 (X/(V T))^0.6 plus half the permanent
    0.314 X/V."""
    t = 1.0 / steps_per_day
    return 0.142 * (f / t) ** 0.6 + 0.5 * 0.314 * f


def fit(rows: list[dict], key: str) -> tuple[float, float]:
    """Least squares of log(cost) on log(f), sizes of 1% and up, both sides:
    the exponent and the coefficient at f = 1 in units of sigma."""
    xs, ys = [], []
    for r in rows:
        if r["f"] >= 0.01 and r[key] > 0 and r["filled_share"] > 0.999:
            xs.append(math.log(r["f"]))
            ys.append(math.log(r[key] / (r["sigma"] * 1e4)))
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    return b, math.exp(my - b * mx)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--names", type=int, default=40)
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--coefficient", type=float, default=0.75)
    ap.add_argument("--exponent", type=float, default=0.5)
    ap.add_argument("--half-life", type=float, default=27.0)
    ap.add_argument("--gamma", type=float, default=0.314,
                    help="fill_impact_coefficient for the refill arm")
    ap.add_argument("--out", default=None)
    ap.add_argument("--base", default="pt-v19",
                    help="the preset whose market the book is measured in")
    args = ap.parse_args(argv)
    global BASE
    BASE = args.base

    result: dict = {"coefficient": args.coefficient, "exponent": args.exponent,
                    "seeds": args.seeds, "names": args.names, "days": args.days,
                    "base": args.base}
    for label, coef in (("off", 0.0), ("on", args.coefficient)):
        rows = []
        for k in range(args.seeds):
            u = tf.Universe.random(args.names, seed=111 + k)
            e, sig = warm(1000 + k, u, model(coef, args.exponent), args.days)
            for i, t in enumerate(e.tickers):
                rows.extend(cost_row(e, i, t, u[i].avg_volume, sig[i]))
        table = {}
        for f in SIZES:
            at = [r for r in rows if r["f"] == f]
            full = [r for r in at if r["filled_share"] > 0.999]
            table[f] = {
                "filled_in_full": len(full) / max(1, len(at)),
                "median_filled_share": statistics.median(r["filled_share"] for r in at),
                "avg_bp": statistics.median(r["avg_bp"] for r in full) if full else None,
                "worst_bp": statistics.median(r["worst_bp"] for r in full) if full else None,
                "avg_Y": statistics.median(r["avg_Y"] for r in full) if full else None,
                "worst_Y": statistics.median(r["worst_Y"] for r in full) if full else None,
                "almgren_sigma_units": almgren_cost(f),
            }
            # Volatility slope: log cost on log sigma across names.
            if len(full) > 10:
                xs = [math.log(r["sigma"]) for r in full]
                ys = [math.log(max(r["avg_bp"], 1e-6)) for r in full]
                mx, my = statistics.fmean(xs), statistics.fmean(ys)
                table[f]["vol_elasticity"] = (
                    sum((x - mx) * (y - my) for x, y in zip(xs, ys))
                    / sum((x - mx) ** 2 for x in xs))
        entry = {"sizes": {str(f): v for f, v in table.items()}}
        if label == "on":
            entry["fit_avg"] = dict(zip(("exponent", "coefficient"), fit(rows, "avg_bp")))
            entry["fit_worst"] = dict(zip(("exponent", "coefficient"), fit(rows, "worst_bp")))
            entry["sigma_median"] = statistics.median(r["sigma"] for r in rows)
        result[label] = entry

    # Volatility regimes: the same names with the fear gauge held at 15 and
    # at 35 for the whole warm-up, so each name's realised volatility is
    # the regime's. The law says cost is proportional to sigma, so the
    # ratio of the two regimes' costs should follow the ratio of their
    # volatilities once the order is past the spread.
    regimes = {}
    for vix in (15.0, 35.0):
        costs: dict[float, list[float]] = {f: [] for f in (0.01, 0.1, 0.3)}
        sigmas = []
        for k in range(args.seeds):
            u = tf.Universe.random(args.names, seed=111 + k)
            e, sig = warm(1000 + k, u, model(args.coefficient, args.exponent),
                          args.days, vix=vix)
            sigmas.extend(sig)
            for i, t in enumerate(e.tickers):
                for r in cost_row(e, i, t, u[i].avg_volume, sig[i]):
                    if r["f"] in costs and r["filled_share"] > 0.999:
                        costs[r["f"]].append(r["avg_bp"])
        regimes[vix] = {"sigma_median": statistics.median(sigmas),
                        "avg_bp": {str(f): statistics.median(v)
                                   for f, v in costs.items()}}
    result["regimes"] = regimes

    u = tf.Universe.random(args.names, seed=111)
    result["refill_extra_bp"] = {
        str(f): refill(1000, u, args.coefficient, args.exponent, f,
                       args.half_life, args.gamma)
        for f in (0.03, 0.1, 0.3)}

    print(f"latent depth Y={args.coefficient} delta={args.exponent}; "
          f"{args.seeds} seeds x {args.names} names, sigma realised over {args.days} days")
    print(" f of V   off: filled  |  on: avg bp  worst bp   avg/(s sqrt f)  worst/(s sqrt f)"
          "  vol elast   Almgren avg (s units)")
    for f in SIZES:
        a, b = result["off"]["sizes"][str(f)], result["on"]["sizes"][str(f)]
        fmt = lambda v, w=8, p=2: (f"{v:{w}.{p}f}" if v is not None else " " * (w - 1) + "-")
        print(f"{f:7.3f}   {a['median_filled_share']:8.3f}     |  {fmt(b['avg_bp'])}  "
              f"{fmt(b['worst_bp'])}   {fmt(b['avg_Y'], 10, 3)}       {fmt(b['worst_Y'], 10, 3)}"
              f"     {fmt(b.get('vol_elasticity'), 7, 2)}     {b['almgren_sigma_units']:.3f}")
    print("fit, 1% and up, both sides: avg cost exponent "
          f"{result['on']['fit_avg']['exponent']:.3f} coefficient "
          f"{result['on']['fit_avg']['coefficient']:.3f}; worst price exponent "
          f"{result['on']['fit_worst']['exponent']:.3f} coefficient "
          f"{result['on']['fit_worst']['coefficient']:.3f}")
    print("literature: peak Y in [0.5, 1] (Toth et al. 2011); average cost "
          "two thirds of that, [0.33, 0.67]")
    lo, hi = result["regimes"][15.0], result["regimes"][35.0]
    print("volatility regimes, VIX held at 15 and 35: realised sigma "
          f"{lo['sigma_median'] * 1e4:.0f} and {hi['sigma_median'] * 1e4:.0f} bp "
          f"(ratio {hi['sigma_median'] / lo['sigma_median']:.2f}); median average cost")
    for f in ("0.01", "0.1", "0.3"):
        print(f"  f={f}: {lo['avg_bp'][f]:.2f} and {hi['avg_bp'][f]:.2f} bp "
              f"(ratio {hi['avg_bp'][f] / lo['avg_bp'][f]:.2f})")
    print("refill (half-life %g ticks, gamma %g): extra bp a second order pays "
          "k ticks after a first, over no first" % (args.half_life, args.gamma))
    for f, parts in result["refill_extra_bp"].items():
        for part, row in parts.items():
            print(f"  f={f} {part:9s}: " + "  ".join(f"k={k}: {v:6.2f}" for k, v in row.items()))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=1, sort_keys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
