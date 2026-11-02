"""Probe 1b: intraday rules on 65-minute bars.

  event_follow  after the first bar, long the names whose first-bar return is
                in the top quintile and short the bottom, hold to the close
                (company news arrives at the open and 12% of it drifts in over
                hours: MODEL.md, News)
  bar_reversal  every bar, long the previous bar's losers, short its winners
  bar_momentum  the opposite
  bar_means     mean return by bar of the day, and close-to-open (none: there
                is no overnight move)

Costs: each leg's entry and exit pay the half-spread plus 1 bp.
Usage: python intraday.py short|var
"""
import sys

import numpy as np

from strategies import load


def bars_with_open(m):
    D, B, N = m["b65"].shape
    closes = m["b65"]
    opens = m["open"]
    prev = np.concatenate([opens[:, None, :], closes[:, :-1, :]], axis=1)
    return prev, closes


def event_follow(m, q=0.2):
    prev, closes = bars_with_open(m)
    r0 = closes[:, 0] / prev[:, 0] - 1
    rest = closes[:, -1] / closes[:, 0] - 1
    D, N = r0.shape
    k = max(1, int(N * q))
    out = []
    for d in range(D):
        o = np.argsort(r0[d])
        lo, hi = o[:k], o[-k:]
        g = 0.5 * (rest[d, hi].mean() - rest[d, lo].mean())
        c = (m["hspread"][d, hi].mean() + m["hspread"][d, lo].mean()) + 2e-4
        out.append(g - c)
    return np.array(out)


def bar_rule(m, sign=+1, q=0.2):
    prev, closes = bars_with_open(m)
    r = closes / prev - 1  # D x B x N
    D, B, N = r.shape
    flat = r.reshape(D * B, N)
    k = max(1, int(N * q))
    out = []
    hs = np.repeat(m["hspread"], B, axis=0)
    for t in range(1, D * B):
        o = np.argsort(flat[t - 1])
        lo, hi = o[:k], o[-k:]
        g = 0.5 * (flat[t, lo].mean() - flat[t, hi].mean()) * sign
        c = hs[t, lo].mean() + hs[t, hi].mean() + 2e-4
        out.append(g - c)
    return np.array(out)


def bar_means(m):
    prev, closes = bars_with_open(m)
    r = np.log(closes / prev)
    return r.mean(axis=(0, 2)) * 1e4, np.log(m["open"][1:] / m["close"][:-1]).mean() * 1e4


if __name__ == "__main__":
    ms = load(sys.argv[1] if len(sys.argv) > 1 else "short")
    ef = np.array([event_follow(m).mean() for m in ms]) * 1e4
    print(f"event_follow: mean {ef.mean():+.2f} bp/day net, positive {np.sum(ef > 0)}/{len(ef)}, t {ef.mean()/ef.std()*np.sqrt(len(ef)):.2f}")
    ef0 = np.array([(event_follow(m) + 0).mean() for m in ms])
    for sgn, name in ((+1, "bar_reversal"), (-1, "bar_momentum")):
        x = np.array([bar_rule(m, sgn).mean() for m in ms]) * 1e4
        print(f"{name}: mean {x.mean():+.2f} bp/bar net, positive {np.sum(x > 0)}/{len(x)}")
    bm = np.array([bar_means(m)[0] for m in ms])
    print("mean log return by bar (bp):", np.round(bm.mean(0), 2), "sd across markets", np.round(bm.std(0), 2))
    print("close-to-open gap mean (bp):", np.mean([bar_means(m)[1] for m in ms]))
    # gross event follow without costs
    gross = []
    for m in ms:
        prev, closes = bars_with_open(m)
        r0 = closes[:, 0] / prev[:, 0] - 1
        rest = closes[:, -1] / closes[:, 0] - 1
        big = np.abs(r0) > 3 * np.nanstd(r0)
        gross.append(np.nanmean(np.sign(r0[big]) * rest[big]) * 1e4 if big.any() else np.nan)
    print("after a first-bar move beyond 3 sd, same-sign rest-of-day move (bp, gross):", np.nanmean(gross))
