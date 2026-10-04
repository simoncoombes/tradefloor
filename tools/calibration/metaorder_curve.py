"""Measure a metaorder's cost and its impact on the tape, against the square-root law.

    python tools/calibration/metaorder_curve.py run SEED [SEED ...] [--names 12]
        [--preset pt-v20] [--over k=v,...] [--out FILE]
    python tools/calibration/metaorder_curve.py report FILE [FILE ...] [--json OUT]
    python tools/calibration/metaorder_curve.py trips SEED [--over k=v,...]
    python tools/calibration/metaorder_curve.py mark SEED [--over k=v,...]

`impact_curve.py` measures one immediate order against the book an agent
meets (row C9). This tool measures what a METAORDER does: an order of
`Q = f V` shares executed as a block or sliced over an hour, half a day or
a day, forked against a twin that does not trade, on the same draws.

`run`, per seed: `Universe.random(40, seed=111)`, 60 untraded sessions to
read each name's realised daily sigma (close to close, the convention of
`impact_curve.py`), then the next session opened and run 15 ticks. From that
state, for `--names` names spread by market cap, every size f in 0.3%, 1%,
3%, 10% and 30% of daily volume and every schedule below, two forks: one
trades and one does not. In units of the name's daily sigma:

- `is_`: implementation shortfall, `sum fill (avg - mid) / sum fill mid`,
  `mid` the twin's mid when the slice is sent (spread included);
- `peak_print`, `peak_s`: the displacement one tick after the last slice,
  `log(print / twin's print)`, and the same in the model price `s`;
- `close_*`: at that session's close; `next_*`: 60 ticks into the next
  session; `nclose_*`: at the next session's close; `d5_*`: at the close
  five sessions after the order;
- `traj`: for the day TWAP at 10%, the displacement of `s` after each slice.

Schedules: `block` (one order), `step` (an hour, a slice every 5 ticks),
`half` (half a day, every 10), `day` (a day, every 10), `day1` (a day,
every tick). A tick is a minute.

`report` reads `run`'s files and prints the metaorder rows below, median
over (seed, name), with the literature each is compared with. The real
values come from ANcerno metaorders, which the papers express in the daily
range `(high - low) / open`; on forty large US names, 2015-2025, the range
is 1.14 (median; 1.26 year by year) times the standard deviation of close
to close returns, and the bands below are in close-to-close sigma.

- Q1 the exponent of the print's peak against f, half-day schedule, 1% to
  30%: 0.47 over all ANcerno metaorders, 0.51 on large caps (Zarinelli,
  Treccani, Farmer and Lillo, Market Microstructure and Liquidity 1(2),
  2015, Table 4); about 0.5 (Toth et al., Physical Review X 1, 021006,
  2011); 0.6 (Almgren, Thum, Hauptmann and Li, Risk 18(7), 2005). Band
  [0.4, 0.7].
- Q2 the print's peak at 10%, half-day, over sqrt(0.1): 0.15 to 0.19
  range units in Zarinelli's fit, about 0.4 at participation above 3e-3
  (Bucci, Benzaquen, Lillo and Bouchaud, PRL 122, 108302, 2019), of order
  one in Toth. Band [0.3, 1.0].
- Q3 the share of the final displacement of `s` reached halfway through a
  day TWAP at 10%: sqrt(1/2) = 0.71 for the square-root propagator. Band
  [0.62, 0.82].
- Q4 close over peak of `s`, half-day orders of 3% and up: 0.66 +- 0.04
  (Bucci, Benzaquen, Lillo and Bouchaud, "Slow decay of impact in equity
  markets", 2019, Fig. 1). Band [0.55, 0.80].
- Q5 the next session's close over the peak, same orders: 0.83 of the
  same-day close, about 0.55 (ibid., Fig. 2). Band [0.40, 0.70].
- Q6 the cost of a day TWAP at 10%: Toth's Y of 0.5 to 1 times two thirds,
  0.105 to 0.21; Almgren et al. with their turnover factor, 0.152 to
  0.165. Band [0.10, 0.21].
- Q7 the cost of a day TWAP over a one-hour TWAP at 10%: a longer
  execution of the same size costs less, about T^-0.25 (Bacry, Iuga,
  Lasnier and Lehalle, 2015), 0.63 for a day against an hour. Band
  [0.5, 0.9].
- Q8 and Q9 the cost of a day TWAP over a block's, at 10% and 3%: slicing
  is cheaper than a block. Row C9 reads this book's block as Almgren et
  al.'s execution over a sixth of a day (the horizon its cost matches),
  and their law puts a day at 0.55 to 0.58 of that at 10% (permanent
  term with the turnover factor, plus eta (X/VT)^(3/5)); Bacry et al.'s
  T^-0.25 puts it at 0.64. Band [0.5, 0.8]. Without it an agent learns to
  trade in blocks, the opposite of execution practice.

`trips` is the round-trip guard: on a warmed market, for every name, six
strategies (pump then dump, a pump by one agent name and a dump by
another, block then slice back, a next-day unwind, alternating 1% orders,
and a wash: one agent rests an ask inside the spread and another lifts it
while a third sells a holding into the tape), each closed flat; the guard is that the most profitable one, against the
same orders priced at the twin's mid, stays below zero. `mark` reports what
an agent holding 10% of daily volume gains on its mark at the close for
the cost of buying 0.01% to 1% of daily volume two ticks before it.

Use held-out seeds for calibration (each registration names which);
nothing here reads real data.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys

import numpy as np

import tradefloor as tf

SIZES = (0.003, 0.01, 0.03, 0.1, 0.3)
#: name: (duration in ticks, ticks between slices); 0 is one block.
SCHEDULES = {"block": (0, 0), "step": (60, 5), "half": (180, 10),
             "day": (360, 10), "day1": (360, 1)}
T0 = 15
WARM_DAYS = 60
ROSTER_SEED = 111

#: The registered bands (close-to-close daily sigma); see the module note.
BANDS = {
    "Q1": (0.4, 0.7), "Q2": (0.3, 1.0), "Q3": (0.62, 0.82), "Q4": (0.55, 0.80),
    "Q5": (0.40, 0.70), "Q6": (0.10, 0.21), "Q7": (0.5, 0.9),
    "Q8": (0.5, 0.8), "Q9": (0.5, 0.8),
}
LABELS = {
    "Q1": "print peak exponent in f, half-day, 1-30%",
    "Q2": "print peak at 10% / sqrt(0.1), half-day",
    "Q3": "share of the final s displacement at halfway, day TWAP 10%",
    "Q4": "close / peak of s, half-day, f >= 3%",
    "Q5": "next close / peak of s, half-day, f >= 3%",
    "Q6": "IS of a day TWAP at 10%",
    "Q7": "IS day TWAP / IS one-hour TWAP at 10%",
    "Q8": "IS day TWAP / IS block at 10%",
    "Q9": "IS day TWAP / IS block at 3%",
}


def f64(b) -> np.ndarray:
    return np.frombuffer(b, "<f8")


def parse_over(text: str | None) -> dict[str, float]:
    out: dict[str, float] = {}
    for kv in (text or "").split(","):
        if kv:
            k, v = kv.split("=")
            out[k.strip()] = float(v)
    return out


def model(preset: str, over: dict[str, float]):
    return tf.ModelParams.from_preset(preset, **over) if over else tf.ModelParams.from_preset(preset)


def advance(e, t: int, n: int, close: bool = False) -> int:
    """Run `n` ticks starting `t` ticks after the 09:30 open."""
    if n <= 0:
        return t
    m = 9 * 60 + 30 + t
    e.run_session(m // 60, m % 60, 3, n, close_at_end=close)
    return t + n


def session(e) -> None:
    e.open_market()
    advance(e, 0, 390, True)
    e.close_market()


def pick_names(universe, k: int) -> list[int]:
    mcap = np.array([x.market_cap for x in universe])
    order = np.argsort(mcap)[::-1]
    n = len(universe)
    return [int(order[int(round(r))]) for r in np.linspace(0, n - 1, k)]


def run_seed(seed: int, params, names_k: int, quick: bool = False) -> list[dict]:
    u = tf.Universe.random(40, seed=ROSTER_SEED)
    adv = np.array([x.avg_volume for x in u])
    mcap = np.array([x.market_cap for x in u])
    base = tf.Engine(seed=seed, universe=u, model=params)
    closes = [f64(base.prices()).copy()]
    for _ in range(WARM_DAYS):
        session(base)
        closes.append(f64(base.prices()).copy())
    sig = np.diff(np.log(np.array(closes)), axis=0).std(axis=0)
    base.open_market()
    advance(base, 0, T0)
    rows = []
    for i in pick_names(u, names_k):
        tk = u[i].ticker

        def disp(a, b):
            return (math.log(f64(a.prices())[i] / f64(b.prices())[i]) / sig[i],
                    (f64(a.column("mispricing_s"))[i] - f64(b.column("mispricing_s"))[i]) / sig[i])

        for f in SIZES:
            if quick and f < 0.01:
                continue
            q_total = f * adv[i]
            for sname, (dur, gap) in SCHEDULES.items():
                if quick and sname == "day1":
                    continue
                e, ctl = base.fork(2)
                n = 1 if dur == 0 else dur // gap
                num = den = filled = 0.0
                t = T0
                traj = []
                for k in range(n):
                    mid = ctl.book(tk).mid_price
                    r = e.submit("me", tk, q_total / n)
                    num += r["filled"] * (r["average_price"] - mid)
                    den += r["filled"] * mid
                    filled += r["filled"]
                    if k < n - 1:
                        t = advance(e, t, gap)
                        advance(ctl, t - gap, gap)
                        if sname == "day" and f == 0.1:
                            traj.append(float(f64(e.column("mispricing_s"))[i]
                                              - f64(ctl.column("mispricing_s"))[i]) / sig[i])
                t = advance(e, t, 1)
                advance(ctl, t - 1, 1)
                pk = disp(e, ctl)
                for x in (e, ctl):
                    advance(x, t, 390 - t, True)
                cl = disp(e, ctl)
                for x in (e, ctl):
                    x.close_market()
                    x.open_market()
                    advance(x, 0, 60)
                nx = disp(e, ctl)
                for x in (e, ctl):
                    advance(x, 60, 330, True)
                    x.close_market()
                nc = disp(e, ctl)
                for x in (e, ctl):
                    for _ in range(4):
                        session(x)
                d5 = disp(e, ctl)
                rows.append(dict(
                    seed=seed, name=i, ticker=tk, mcap=float(mcap[i]), sigma=float(sig[i]),
                    f=f, sched=sname, T=dur, n=n, filled_share=filled / q_total,
                    is_=num / den / sig[i] if den else float("nan"),
                    peak_print=pk[0], peak_s=pk[1], close_print=cl[0], close_s=cl[1],
                    next_print=nx[0], next_s=nx[1], nclose_print=nc[0], nclose_s=nc[1],
                    d5_print=d5[0], d5_s=d5[1], traj=traj))
        print(seed, tk, "done", file=sys.stderr, flush=True)
    return rows


def _med(xs):
    xs = [x for x in xs if x == x]
    return statistics.median(xs) if xs else float("nan")


def _fit(fs, ys):
    sel = [(f, y) for f, y in zip(fs, ys) if f >= 0.01 and y > 0]
    if len(sel) < 2:
        return float("nan"), float("nan")
    b, a = np.polyfit([math.log(f) for f, _ in sel], [math.log(y) for _, y in sel], 1)
    return float(b), float(math.exp(a))


def rows_statistics(rows: list[dict]) -> dict:
    """The Q rows and the diagnostics beside them, from `run`'s rows."""
    sizes = sorted({r["f"] for r in rows})

    def at(sched, f):
        return [r for r in rows if r["sched"] == sched and r["f"] == f]

    def per_trial_ratio(num_sched, den_sched, f, key="is_"):
        den = {(r["seed"], r["name"]): r[key] for r in at(den_sched, f)}
        return _med([r[key] / den[(r["seed"], r["name"])] for r in at(num_sched, f)
                     if den.get((r["seed"], r["name"]))])

    out: dict = {"n_rows": len(rows), "seeds": sorted({r["seed"] for r in rows}),
                 "names": sorted({r["name"] for r in rows})}
    curves = {}
    for key in ("is_", "peak_s", "peak_print"):
        curves[key] = {}
        for s in SCHEDULES:
            ys = [_med([r[key] for r in at(s, f)]) for f in sizes]
            b, a = _fit(sizes, ys)
            curves[key][s] = {"by_f": dict(zip(map(str, sizes), ys)), "exponent": b, "coefficient": a}
    out["curves"] = curves
    q = {}
    half_print = curves["peak_print"]["half"]
    q["Q1"] = half_print["exponent"]
    q["Q2"] = half_print["by_f"].get("0.1", float("nan")) / math.sqrt(0.1)
    traj = [r["traj"] for r in at("day", 0.1) if r["traj"]]
    if traj:
        path = np.median(np.array(traj), axis=0)
        end = _med([r["peak_s"] for r in at("day", 0.1)])
        n = len(path) + 1
        # After slice k (1-based) of n the order is k/n of the way; path[k-1].
        out["path"] = {str(frac): float(path[int(round(frac * n)) - 1] / end)
                       for frac in (0.25, 0.5, 0.75)}
        q["Q3"] = out["path"]["0.5"]
    big = [r for r in rows if r["sched"] == "half" and r["f"] >= 0.03 and r["peak_s"] > 0]
    q["Q4"] = _med([r["close_s"] / r["peak_s"] for r in big])
    q["Q5"] = _med([r.get("nclose_s", float("nan")) / r["peak_s"] for r in big])
    q["Q6"] = curves["is_"]["day"]["by_f"].get("0.1", float("nan"))
    q["Q7"] = per_trial_ratio("day", "step", 0.1)
    q["Q8"] = per_trial_ratio("day", "block", 0.1)
    q["Q9"] = per_trial_ratio("day", "block", 0.03)
    out["Q"] = q
    out["decay"] = {
        s: {k: _med([r[f"{k}_s"] / r["peak_s"] for r in rows
                     if r["sched"] == s and r["f"] >= 0.03 and r["peak_s"] > 0])
            for k in ("close", "next", "nclose", "d5") if all(f"{k}_s" in r for r in rows)}
        for s in SCHEDULES}
    out["sliced_over_block"] = {s: {str(f): per_trial_ratio(s, "block", f) for f in sizes}
                                for s in SCHEDULES if s != "block"}
    return out


def report(stats: dict) -> str:
    lines = [f"{stats['n_rows']} rows, {len(stats['seeds'])} seeds, {len(stats['names'])} names; "
             "median over (seed, name), in the name's daily sigma"]
    for row, value in stats["Q"].items():
        lo, hi = BANDS[row]
        verdict = "pass" if lo <= value <= hi else "FAIL"
        lines.append(f"  {row} {LABELS[row]:60s} {value:7.3f}  [{lo}, {hi}]  {verdict}")
    lines.append("curves (median by f; fit over 1% and up):")
    for key, per in stats["curves"].items():
        for s, c in per.items():
            ys = "  ".join(f"{f}:{y:.3f}" for f, y in c["by_f"].items())
            lines.append(f"  {key:10s} {s:5s} {ys}   fit {c['coefficient']:.3f} f^{c['exponent']:.2f}")
    if "path" in stats:
        lines.append("path of s in a day TWAP at 10% (1/4, 1/2, 3/4): "
                     + " ".join(f"{v:.2f}" for v in stats["path"].values())
                     + "  (square root 0.50 0.71 0.87)")
    lines.append("decay of s over its peak, f >= 3% (close, +60 ticks, next close, 5 closes):")
    for s, d in stats["decay"].items():
        lines.append(f"  {s:5s} " + "  ".join(f"{k} {v:.2f}" for k, v in d.items()))
    lines.append("sliced IS over block IS (median of per-trial ratios):")
    for s, d in stats["sliced_over_block"].items():
        lines.append(f"  {s:5s} " + "  ".join(f"{f}:{v:.2f}" for f, v in d.items()))
    return "\n".join(lines)


# ── the round-trip guard ─────────────────────────────────────────────────


def _clock(t: int) -> tuple[int, int]:
    m = 9 * 60 + 30 + t
    return m // 60, m % 60


def warmed(params, seed: int, universe, start: int = 65):
    e = tf.Engine(seed=seed, universe=universe, model=params)
    session(e)
    e.open_market()
    e.run_session(9, 30, 3, start)
    return e


def play(base, i: int, plan: list[tuple], start: int = 65):
    """Run `plan` (tick after the open, possibly past 390 into the next
    session; agent; signed shares; and optionally "inside", a limit order
    resting a cent inside the spread) on a fork, and price the same orders
    on a twin that does not trade. Returns (fed cash, twin cash) or None
    when an order did not fill in full.

    The fed cash is every agent's, read from the fills (a resting order's
    maker fills included), so a strategy run by several agent names is
    scored as one book: two agents trading with each other net to zero.
    The twin prices each order at its own mid when the order is sent."""
    t = base.tickers[i]
    fed, bare = base.fork(2)
    fed.take_fills()
    bare_cash = 0.0
    wanted: dict[str, float] = {}
    now = start  # ticks since the first session's open, across sessions
    for step_plan in sorted(plan, key=lambda x: x[:3]):
        when, agent, q = step_plan[:3]
        kind = step_plan[3] if len(step_plan) > 3 else "market"
        while when > now:
            into = now % 390
            step = min(when - now, 390 - into)
            h, m = _clock(into)
            for x in (fed, bare):
                x.run_session(h, m, 3, step, close_at_end=(into + step == 390))
            now += step
            if now % 390 == 0 and when > now:
                for x in (fed, bare):
                    x.close_market()
                    x.open_market()
        book = bare.book(t)
        if kind == "inside":
            fb = fed.book(t)
            px = round(fb.best_ask - 0.01, 2) if q < 0 else round(fb.best_bid + 0.01, 2)
            if (q < 0 and px <= fb.best_bid) or (q > 0 and px >= fb.best_ask):
                px = fb.best_ask if q < 0 else fb.best_bid
            r = fed.submit(agent, t, q, limit_price=px)
            got = abs(q)
        else:
            r = fed.submit(agent, t, q)
            c = book.sweep_cost("buy" if q > 0 else "sell", abs(q))
            got = c.filled
            if abs(r["filled"] - c.filled) > 1e-6:
                return None
        wanted[r["order_id"]] = abs(q)
        bare_cash -= math.copysign(1, q) * got * book.mid_price
    fills = fed.take_fills()
    done: dict[str, float] = {}
    fed_cash = 0.0
    for f in fills:
        if f["ticker"] != t:
            continue
        done[f["order_id"]] = done.get(f["order_id"], 0.0) + f["quantity"]
        fed_cash -= (1 if f["side"] == "buy" else -1) * f["quantity"] * f["price"]
    if any(abs(done.get(k, 0.0) - v) > 1e-6 for k, v in wanted.items()):
        return None
    return fed_cash, bare_cash


def strategies(volume: float) -> dict[str, list[tuple]]:
    """The round trips, each flat at the end (every agent, or the agents
    of a strategy taken together); ticks after the open, 65 the first."""
    out = {}
    for f in (0.03, 0.1, 0.3):
        big = round(f * volume)
        s = round(big / 6)
        out[f"pump{f}"] = [(65 + 10 * k, "a", s) for k in range(6)] + [(126, "a", -6 * s)]
        out[f"collude{f}"] = ([(65 + 10 * k, "a", s) for k in range(6)] + [(126, "b", -6 * s)]
                              + [(127 + 10 * k, "a", -s) for k in range(6)] + [(190, "b", 6 * s)])
        out[f"blockslice{f}"] = [(65, "a", 6 * s)] + [(66 + 10 * k, "a", -s) for k in range(6)]
        out[f"nextday{f}"] = [(65, "a", big), (390 + 65, "a", -big)]
    q = round(0.01 * volume)
    out["alt0.01"] = [(65 + k, "a", q if k % 2 == 0 else -q) for k in range(10)]
    # Wash: c buys, then a rests an ask a cent inside the spread and b lifts
    # it, twenty times at 0.5% of volume; c then sells into whatever the
    # wash left on the tape. a and b end flat together, c flat alone.
    w = round(0.005 * volume)
    for f in (0.02, 0.1):
        hold = round(f * volume)
        out[f"wash{f}"] = ([(65, "c", hold)]
                           + [(66 + k, "a", -w, "inside") for k in range(20)]
                           + [(66 + k, "b", w) for k in range(20)]
                           + [(87, "c", -hold)])
    return out


def round_trips(seed: int, params, n_names: int = 20, roster_seed: int = 93001) -> list[dict]:
    """Every strategy on every name: `edge_bp` is the fed cash less the
    twin's, over the gross notional of one leg, in bp. Negative is a loss."""
    u = tf.Universe.random(n_names, seed=roster_seed)
    base = warmed(params, seed, u)
    prices = f64(base.prices())
    rows = []
    for i in range(len(u)):
        for name, plan in strategies(u[i].avg_volume).items():
            r = play(base, i, plan)
            if r is None:
                continue
            fed, bare = r
            notional = sum(abs(x[2]) for x in plan) / 2 * prices[i]
            rows.append(dict(seed=seed, name=i, strategy=name,
                             edge_bp=(fed - bare) / notional * 1e4, pnl_bp=fed / notional * 1e4))
    return rows


def mark_close(seed: int, params, n_names: int = 20, roster_seed: int = 93001) -> dict:
    """Median, over names, of the gain on a 10%-of-volume holding's mark at
    this close and the next, over the cost of buying `p` of daily volume
    two ticks before the close."""
    u = tf.Universe.random(n_names, seed=roster_seed)
    e = tf.Engine(seed=seed, universe=u, model=params)
    session(e)
    e.open_market()
    e.run_session(9, 30, 3, 388)
    out = {}
    for p in (0.0001, 0.001, 0.01):
        gain, nxt = [], []
        for i in range(len(u)):
            t = u[i].ticker
            v = u[i].avg_volume
            hold = 0.1 * v
            x, ctl = e.fork(2)
            mid = ctl.book(t).mid_price
            r = x.submit("me", t, max(1, round(p * v)))
            cost = r["filled"] * (r["average_price"] - mid)
            for y in (x, ctl):
                y.run_session(15, 58, 3, 2, close_at_end=True)
            g = hold * (f64(x.prices())[i] - f64(ctl.prices())[i])
            for y in (x, ctl):
                y.close_market()
                y.open_market()
                y.run_session(9, 30, 3, 390, close_at_end=True)
            n = hold * (f64(x.prices())[i] - f64(ctl.prices())[i])
            if cost > 0:
                gain.append(g / cost)
                nxt.append(n / cost)
        out[str(p)] = {"close_gain_over_cost": _med(gain), "next_close_gain_over_cost": _med(nxt)}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("seeds", type=int, nargs="+")
    r.add_argument("--names", type=int, default=12)
    r.add_argument("--preset", default="pt-v20")
    r.add_argument("--over", default="")
    r.add_argument("--out", default=None)
    r.add_argument("--quick", action="store_true",
                   help="a screen: leave out 0.3%% of volume and the one-tick day schedule")
    p = sub.add_parser("report")
    p.add_argument("files", nargs="+")
    p.add_argument("--json", default=None)
    for name in ("trips", "mark"):
        x = sub.add_parser(name)
        x.add_argument("seeds", type=int, nargs="+")
        x.add_argument("--preset", default="pt-v20")
        x.add_argument("--over", default="")
        x.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    if args.cmd == "report":
        rows = [row for f in args.files for row in json.load(open(f))]
        stats = rows_statistics(rows)
        print(report(stats))
        if args.json:
            json.dump(stats, open(args.json, "w"), indent=1)
        return 0

    params = model(args.preset, parse_over(args.over))
    if args.cmd == "run":
        rows = [row for s in args.seeds for row in run_seed(s, params, args.names, args.quick)]
        if args.out:
            json.dump(rows, open(args.out, "w"))
        print(report(rows_statistics(rows)))
        return 0
    if args.cmd == "trips":
        rows = [row for s in args.seeds for row in round_trips(s, params)]
        worst = max(rows, key=lambda x: x["edge_bp"])
        for name in sorted({x["strategy"] for x in rows}):
            g = [x["edge_bp"] for x in rows if x["strategy"] == name]
            print(f"  {name:16s} n={len(g):3d} edge mean {statistics.fmean(g):8.1f} bp  max {max(g):8.1f} bp")
        print(f"G-rt: best round trip {worst['edge_bp']:.2f} bp ({worst['strategy']}, name {worst['name']}); "
              f"{'pass' if worst['edge_bp'] < 0 else 'FAIL'}")
        if args.out:
            json.dump(rows, open(args.out, "w"))
        return 0 if worst["edge_bp"] < 0 else 1
    if args.cmd == "mark":
        res = {str(s): mark_close(s, params) for s in args.seeds}
        print(json.dumps(res, indent=1))
        if args.out:
            json.dump(res, open(args.out, "w"))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
