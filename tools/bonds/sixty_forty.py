"""A 60/40 portfolio and its rebalancer through every packaged scenario.

The question this answers is a head of risk's: what does a rate shock do to a
60/40 book, and what does the rebalancer do about it. Each run holds 60% in
the roster's equities, equally weighted, and 40% in the simulated rate
indices (10% UST2Y, 20% UST10Y, 10% IGCORP, a sleeve duration of 6.5 years),
through `tradefloor.baselines.Balanced`, under three policies: never
rebalance, rebalance past a 5 point drift, rebalance past a 2 point drift.

The loop is the harness's (`tradefloor.evaluate`) written out, so it can mark
the book at every close: scenario pins, open, act, execute against the book,
run the session with the fills, close. One decision a day, at the open.

Reported per scenario and policy, as the median over seeds: the day the
first shock lands (close to close), the worst drawdown from the close before
it, the return to the end of the run, what each sleeve did from the close
before the shock to the end (time-weighted, so a rebalance moving money
between sleeves is not counted as a return), the bond sleeve's weight at the
open the shock landed on, which is what the rebalancer saw, how often the rebalancer traded, and what its trades
after day zero cost against the price it saw before trading, in basis points
of the starting book.

    python tools/bonds/sixty_forty.py --seeds 1 2 3 4 5 6 --days 120 --workers 6
"""

from __future__ import annotations

import argparse
import json
import statistics
import struct
from concurrent.futures import ThreadPoolExecutor

import tradefloor as tf
from tradefloor.baselines import Balanced, RATE_TICKERS
from tradefloor.harness import Observation

POLICIES = {"held": None, "band 5pt": 0.05, "band 2pt": 0.02}


def _f64(buf: bytes) -> list[float]:
    return list(struct.unpack("<%dd" % (len(buf) // 8), buf))


def run(universe, seed: int, scenario_name: str | None, band: float | None,
        days: int) -> dict:
    scenario = None if scenario_name is None else tf.Scenario.load(scenario_name)
    engine = tf.Engine(seed=seed, universe=universe)
    portfolio = tf.Portfolio(cash=1_000_000.0, max_leverage=2.0)
    agent = Balanced(band=band)
    tickers = engine.tickers
    adv = [inst.avg_volume for inst in universe]
    closes = []
    # Each sleeve's time-weighted growth: its holdings' move from the last
    # close to the open, then from the open, after the day's trades, to the
    # close. A rebalance moves money between sleeves, and a sleeve's value
    # alone would count that as a return.
    growth = {"equity": [1.0], "bonds": [1.0]}
    held_at_close = {"equity": 0.0, "bonds": 0.0}
    cost = rebalance_cost = 0.0

    def sleeves(prices_now):
        out = {"equity": 0.0, "bonds": 0.0}
        for t, p in portfolio.positions.items():
            key = "bonds" if t in RATE_TICKERS else "equity"
            out[key] += p.quantity * prices_now[tickers.index(t)]
        return out

    for day in range(days):
        if scenario is not None:
            scenario.apply(engine, day)
            adv = _f64(engine.column("avg_volume"))
        engine.open_market()
        prices = _f64(engine.prices())
        before_trades = sleeves(prices)
        portfolio.stamp(day, day, 0)
        obs = Observation(day, day, tickers, prices, portfolio, engine, adv, 1)
        for ticker, quantity in (agent.act(obs) or {}).items():
            fill = portfolio.execute(engine, ticker, quantity)
            seen = prices[tickers.index(ticker)]
            paid = fill["quantity"] * (fill["price"] - seen)
            cost += paid
            if day > 0:
                rebalance_cost += paid
        after_trades = sleeves(prices)
        engine.run_session(9, 30, 3, 390, fills=portfolio.pending_flow())
        portfolio.clear_flow()
        engine.close_market()
        closes.append(portfolio.net_worth(engine))
        at_close = sleeves(_f64(engine.prices()))
        for key in growth:
            step = 1.0
            if held_at_close[key] > 0:
                step *= before_trades[key] / held_at_close[key]
            if after_trades[key] > 0:
                step *= at_close[key] / after_trades[key]
            growth[key].append(growth[key][-1] * step)
        held_at_close = at_close
    at = 50 if scenario is None else min(i.at for i in scenario.interventions)
    before = closes[at - 1]
    returns = [closes[d] / closes[d - 1] - 1.0 for d in range(1, days)]
    peak, drawdown = closes[0], 0.0
    for value in closes:
        peak = max(peak, value)
        drawdown = min(drawdown, value / peak - 1.0)
    return {
        "shock_day": closes[at] / before - 1.0,
        "worst_after": min(closes[at:]) / before - 1.0,
        "to_end": closes[-1] / before - 1.0,
        "run_return": closes[-1] / 1_000_000.0 - 1.0,
        "max_drawdown": drawdown,
        "vol": statistics.pstdev(returns) * 252 ** 0.5,
        "equity_sleeve": growth["equity"][-1] / growth["equity"][at] - 1.0,
        "bond_sleeve": growth["bonds"][-1] / growth["bonds"][at] - 1.0,
        # What the rebalancer saw at the open the shock landed on.
        "bond_weight_after": agent.marks[at][2] / sum(agent.marks[at][1:]),
        "rebalances": len(agent.rebalances),
        "first_rebalance": min((d for d in agent.rebalances if d >= at), default=None),
        "cost_bps": cost / sum(abs(f["notional"]) for f in portfolio.fills) * 1e4,
        "rebalance_cost_bps": rebalance_cost / 1_000_000.0 * 1e4,
        "at": at,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3, 4, 5, 6])
    parser.add_argument("--days", type=int, default=120)
    parser.add_argument("--names", type=int, default=40)
    parser.add_argument("--roster-seed", type=int, default=111)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--json", help="write every run's numbers here")
    args = parser.parse_args()

    universe = tf.Universe.random(args.names, seed=args.roster_seed, bonds=True)
    scenarios = [None, *tf.Scenario.available()]
    jobs = [(name, policy, seed) for name in scenarios for policy in POLICIES
            for seed in args.seeds]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(
            lambda job: run(universe, job[2], job[0], POLICIES[job[1]], args.days),
            jobs))
    table: dict = {}
    for (name, policy, seed), row in zip(jobs, results):
        table.setdefault(name or "none", {}).setdefault(policy, []).append(row)

    def med(rows, key):
        values = [r[key] for r in rows if r[key] is not None]
        return statistics.median(values) if values else float("nan")

    print(f"60/40 on Universe.random({args.names}, seed={args.roster_seed}, "
          f"bonds=True), {args.days} days, seeds {args.seeds}, model "
          f"{tf.model_preset()['name']}; medians over seeds")
    print(f"{'scenario':22s} {'policy':9s} {'shock day':>9s} {'worst':>8s} "
          f"{'to end':>8s} {'eq sleeve':>9s} {'bond sl.':>9s} {'bond wt':>8s} "
          f"{'rebal':>5s} {'rb cost':>7s} {'vol':>6s} {'maxDD':>7s}")
    for name, policies in table.items():
        for policy, rows in policies.items():
            print(f"{name:22s} {policy:9s} {med(rows, 'shock_day') * 100:+8.2f}% "
                  f"{med(rows, 'worst_after') * 100:+7.2f}% "
                  f"{med(rows, 'to_end') * 100:+7.2f}% "
                  f"{med(rows, 'equity_sleeve') * 100:+8.2f}% "
                  f"{med(rows, 'bond_sleeve') * 100:+8.2f}% "
                  f"{med(rows, 'bond_weight_after') * 100:7.1f}% "
                  f"{med(rows, 'rebalances'):5.1f} {med(rows, 'rebalance_cost_bps'):7.3f} "
                  f"{med(rows, 'vol') * 100:5.1f}% {med(rows, 'max_drawdown') * 100:+6.2f}%")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as out:
            json.dump(table, out, indent=1)


if __name__ == "__main__":
    main()
