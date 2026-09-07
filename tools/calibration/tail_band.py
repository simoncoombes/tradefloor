"""The real-market band for the panel's index tail row, derived from ^GSPC.

The row `index_tail_dn3_pct` in `tradefloor.facts` reports the share of
sessions, in per cent, whose cap-weighted index close-to-close return is at
or below -3 per cent. It is a COUNT row: its graded value over the
certification seeds is the pooled rate, the hits over every seed divided by
the sessions over every seed, and never a median across seeds. Thirteen of
the thirty-five real 252-return windows since 1990 hold no such session at
all, so a median across seeds is an order statistic on a distribution with
a third of its mass at zero and cannot see the quantity the row names.

WHICH REAL SERIES. ^GSPC on the UNADJUSTED close, the fourth column of the
cached row: an index pays no dividend, and the model's session return
carries none either. The reference panel's own rows
(`facts.REAL_MARKETS_WINDOWS`) are pooled PER-NAME log returns and measure
a different object, so this row's real side is not derivable from them and
does not live in that table.

THE BAND is the level row's form and not the shape rows'. The row grades a
MEAN over the certification seeds, so the fidelity question is "is the
model's ensemble rate consistent with the real long-run rate, given how
well the tape knows it", not "could one real year read this". `BAND_RULE`
answers the second: on these windows it gives a floor below zero and a
ceiling that admits 2008 every year.

    centre    c    = the mean over the windows of each window's rate
    scale     se_R = the across-window sd / sqrt(number of windows)
    multiplier m   = facts.centre_multiplier(facts.band_rule_tolerance(9))
    band           = [c - m se_R, c + m se_R], each edge rounded outward

Nothing in it is chosen. The multiplier is the two-sided normal point at
`BAND_RULE`'s own measured false-alarm rate, so the band is exactly as
tolerant of a correct model as the fidelity band on every other row.

RESIDUAL, printed rather than removed. One window, the one straddling
2008-2009, carries 33 of the 107 hits. Dropping it from both the centre and
the scale gives a different quantity -- the non-2008 crash rate -- and a
model that never produces a 2008 would pass it. The band this tool derives
says "the unconditional crash rate, 2008 included, known to about a third
of itself at one standard error", which is the true state of thirty-five
years of evidence.

THE WINDOW ANCHOR is `ANCHOR` below, named and commented rather than left
implicit in a slice: it decides the band edges and the note this was built
from did not state it.

Usage:  python tools/calibration/tail_band.py [--emit]

`--emit` prints the window table as the Python literal `facts` carries in
`INDEX_TAIL_WINDOWS`; `tests/test_reference_windows.py` re-derives the
shipped band from that table rather than trusting that they match.
"""
import argparse
import math
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shadow"))
import data  # noqa: E402

# The band rule's tolerance, the multiplier it implies and the outward
# rounding, from the one place that defines them.
from tradefloor.facts import (  # noqa: E402
    MEDIAN_SE_FACTOR, band_rule_tolerance, centre_multiplier, round_outward,
    shared_rule, trimmed_sd,
)

#: The fetch window, pinned. `wsd3-realsd.md` asked for a pinned end after a
#: rate whose denominator moved with the fetch date was read as a finding:
#: 107 hits over a session count that grew every time the cache was
#: refreshed. Yahoo answers a request end with the last session strictly
#: before it, so `2025-08-01` is the string that names the cache file and
#: `LAST_BAR` is the date the series actually ends on -- the date the
#: window labels below use. Both are pinned, and a cache ending anywhere
#: else is refused rather than read as this one.
START, END = "1990-01-01", "2025-08-01"
LAST_BAR = "2025-07-31"

SYMBOL = "^GSPC"
THRESHOLD = -3.0
#: The two certified horizons, in RETURNS per window.
HORIZONS = (252, 504)


def closes():
    """The unadjusted ^GSPC closes of the pinned cache, oldest first."""
    d = data.fetch(SYMBOL, START, END)
    rows = [r for r in d["rows"] if len(r) > 3 and r[3] is not None and r[3] > 0]
    if rows[-1][0] != LAST_BAR:
        raise SystemExit(
            f"the cache ends at {rows[-1][0]} and this derivation is pinned "
            f"to {LAST_BAR}; a rate whose denominator moves with the fetch "
            "date is the fault this pin exists to prevent")
    return rows, d


#: THE ANCHOR RULE, named because a band whose anchoring is implicit is not
#: reproducible, and that is the failure this row exists to prevent.
#:
#: The windows are non-overlapping blocks of `length` consecutive RETURNS,
#: anchored at the LATEST return and walking back; the remainder at the
#: start of the series is dropped. Two things follow and both are load
#: bearing.
#:
#: The anchor is the tape's last bar, which is the one fixed point that does
#: not move when the cache is refetched. Anchoring at the first bar instead
#: would silently discard the most recent data and would move every window
#: each time the series grew.
#:
#: The windows are contiguous in RETURN space, so no session falls between
#: two of them. Blocking the BARS instead -- 253-bar blocks giving 252
#: returns each -- leaves one seam return unused at every boundary, 34 of
#: them at 35 windows, and those seams are sessions the tape holds and the
#: derivation would not count. The two constructions read the same 107 hits
#: in 8,820 sessions and the same centre, and they differ in the across-
#: window sd (2.3613 here against 2.3711 bar-blocked) because the hits fall
#: into different windows, which is enough to move a rounded band edge.
#:
#: The dropped lead-in is DERIVED from the series length and not chosen:
#: `len(returns) - count * length`, 140 returns at 35 windows of 252 on the
#: pinned cache.
ANCHOR = ("non-overlapping blocks of consecutive returns, anchored at the "
          "latest return; the remainder at the start of the series is "
          "dropped")


def session_returns(rows):
    """`(session date, return per cent)` per pair of consecutive closes.

    The date is the LATER bar's: a return belongs to the session that closed
    it, and that is the session a hit is counted in.
    """
    return [(rows[i][0], (rows[i][3] / rows[i - 1][3] - 1.0) * 100.0)
            for i in range(1, len(rows))]


def windows(series, length):
    """`ANCHOR` applied to a return series: the blocks, and the lead-in dropped."""
    count = len(series) // length
    lead_in = len(series) - count * length
    return ([series[lead_in + i * length:lead_in + (i + 1) * length]
             for i in range(count)], lead_in)


def excess_kurtosis(values):
    """The population fourth standardised moment less three, `facts`'s form."""
    mean = statistics.fmean(values)
    sd = statistics.pstdev(values)
    if sd == 0:
        return 0.0
    standard = [(x - mean) / sd for x in values]
    return sum(x ** 4 for x in standard) / len(standard) - 3.0


def table(series, length):
    """One row per window: (start, end, hits, sessions, rate, sd, exkurt).

    `start` and `end` are the first and last SESSION in the window, not the
    bars either side of it: the window is a block of returns and its label
    names the sessions it counts.
    """
    blocks, lead_in = windows(series, length)
    out = []
    for block in blocks:
        rets = [r for _, r in block]
        hits = sum(1 for r in rets if r <= THRESHOLD)
        out.append((block[0][0], block[-1][0], hits, len(rets),
                    100.0 * hits / len(rets), statistics.pstdev(rets),
                    excess_kurtosis(rets)))
    return out, lead_in


def block_bootstrap(values, length, draws=200_000, seed=20260905):
    """The sd of the mean under a moving-block resample at `length`.

    The iid standard error assumes the windows are exchangeable. They are
    not obviously so -- a crash can straddle a boundary -- and this prices
    that: blocks of `length` consecutive windows are drawn with replacement
    until the resample is as long as the original, and the sd of the mean
    over `draws` resamples is the answer. At length one it IS the ordinary
    nonparametric bootstrap, whose value is known in closed form,
    `sd sqrt((n-1)/n) / sqrt(n)` = 0.3950 on these thirty-five rates, so
    the length-one column is this estimator's own accuracy check.

    `draws` and `seed` are `facts.band_rule_false_alarm`'s, the other Monte
    Carlo whose answer feeds this band, rather than the 2,000 the effect-size
    bootstraps use: on a sample this skewed 2,000 draws read 0.3817 against
    the closed form's 0.3950, which is a third of the difference the column
    exists to detect. At 200,000 the length-one column reads 0.3944.
    """
    rng = random.Random(seed)
    n = len(values)
    starts = n - length + 1
    means = []
    for _ in range(draws):
        sample = []
        while len(sample) < n:
            start = rng.randrange(starts)
            sample.extend(values[start:start + length])
        means.append(statistics.fmean(sample[:n]))
    return statistics.stdev(means)


def lag1(values):
    mean = statistics.fmean(values)
    variance = sum((x - mean) ** 2 for x in values)
    if variance == 0:
        return 0.0
    return sum((values[i] - mean) * (values[i - 1] - mean)
               for i in range(1, len(values))) / variance


def band(centre, se, multiplier, key="index_tail_dn3_pct"):
    low, high = centre - multiplier * se, centre + multiplier * se
    return (low, high,
            round_outward(low, "low", key), round_outward(high, "high", key))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit", action="store_true",
                    help="print the window table as facts.INDEX_TAIL_WINDOWS")
    args = ap.parse_args()

    rows, meta = closes()
    series = session_returns(rows)
    pooled = [r for _, r in series]
    down = sum(1 for r in pooled if r <= THRESHOLD)
    up = sum(1 for r in pooled if r >= -THRESHOLD)
    sd = statistics.pstdev(pooled)
    gaussian = 100.0 * statistics.NormalDist().cdf(THRESHOLD / sd)

    print(f"{SYMBOL}, unadjusted close, {rows[0][0]}..{rows[-1][0]}, "
          f"{len(rows)} closes and {len(pooled)} returns")
    print(f"  url {meta['url']}")
    print(f"  fetched {meta['fetched']}")
    print(f"  whole series: {down} sessions at or below {THRESHOLD}% "
          f"({100.0 * down / len(pooled):.4f}%), {up} at or above "
          f"{-THRESHOLD}% ({100.0 * up / len(pooled):.4f}%)")
    print(f"  a Gaussian at the pooled sd of {sd:.4f} would put "
          f"{gaussian:.3f}% below the threshold, a "
          f"{down / len(pooled) / (gaussian / 100.0):.2f}x excess")
    print("  the whole-series rate is NOT the row's centre: the centre is the "
          "mean over the windows the scale is measured on, below")
    print(f"  window anchor: {ANCHOR}")

    multiplier = centre_multiplier(band_rule_tolerance(9))
    print(f"\nmultiplier {multiplier:.6f} = centre_multiplier("
          f"band_rule_tolerance(9) = {band_rule_tolerance(9)}), "
          "BAND_RULE's own measured false-alarm rate")

    emitted = {}
    for horizon in HORIZONS:
        per, lead_in = table(series, horizon)
        rates = [w[4] for w in per]
        counts = [w[2] for w in per]
        hits, sessions = sum(counts), sum(w[3] for w in per)
        centre = statistics.fmean(rates)
        across = statistics.stdev(rates)
        se = across / math.sqrt(len(rates))
        emitted[horizon] = per

        print(f"\n=== {horizon} returns a window, {len(per)} non-overlapping "
              "windows ===")
        print(f"{'window':>26}  {'hits':>4} {'sessions':>8} {'rate %':>8} "
              f"{'sd':>6} {'exkurt':>7}")
        for start, end, k, n, rate, wsd, exk in per:
            print(f"{start + '..' + end:>26}  {k:>4} {n:>8} {rate:>8.3f} "
                  f"{wsd:>6.3f} {exk:>7.2f}")
        print(f"  pooled {hits} hits in {sessions} sessions = "
              f"{100.0 * hits / sessions:.4f}%")
        print(f"  centre (mean of the window rates) {centre:.4f}%, which is "
              "the pooled rate because the windows are of equal length")
        print(f"  median {statistics.median(rates):.4f}%: "
              f"{sum(1 for k in counts if k == 0)} of {len(counts)} windows "
              "hold no hit, which is why this row is not a median")
        print(f"  across-window sd {across:.4f}, se of the mean {se:.4f}")
        print("  block bootstrap of the se: " + ", ".join(
            f"length {length}: {block_bootstrap(rates, length):.4f}"
            for length in (1, 2, 3)))
        print(f"  lag-1 autocorrelation of the counts {lag1(counts):+.4f}")
        print(f"  {sum(1 for k in counts if k == 0)} windows at zero, "
              f"{sum(1 for k in counts if k >= 5)} at five or more, "
              f"maximum {max(counts)}")

        low, high, rlow, rhigh = band(centre, se, multiplier)
        print(f"  raw band [{low:.4f}, {high:.4f}] -> ROUNDED OUTWARD "
              f"[{rlow:.2f}, {rhigh:.2f}] per cent of sessions")
        print(f"  in the centre's own units, {rlow / centre:.2f}x to "
              f"{rhigh / centre:.2f}x")

        # The residual. The window is found by its own hit count rather
        # than by a date, so the sentence cannot go stale if the anchor or
        # the cache moves.
        worst = max(per, key=lambda w: w[2])
        keep = [w[4] for w in per if w is not worst]
        if len(keep) < len(per):
            c2 = statistics.fmean(keep)
            se2 = statistics.stdev(keep) / math.sqrt(len(keep))
            _, _, rl2, rh2 = band(c2, se2, multiplier)
            print(f"  RESIDUAL: the worst window {worst[0]}..{worst[1]} holds "
                  f"{worst[2]} of the {hits} hits; without it "
                  f"the centre falls to {c2:.4f} and the se to {se2:.4f}, "
                  f"band [{rl2:.2f}, {rh2:.2f}] -- a different quantity, the "
                  "non-2008 rate, which a model that never produces a 2008 "
                  "would pass. Shown, not adopted")
        rule_low, rule_high, s = shared_rule(rates)
        print(f"  BAND_RULE on the same rates gives [{rule_low:.2f}, "
              f"{rule_high:.2f}] at a trimmed sd of {s:.3f}: a prediction "
              "interval for ONE real year, whose floor is below zero and "
              "whose ceiling admits 2008 every year. Not the form for a mean")

        exk = [w[6] for w in per]
        print(f"  index excess kurtosis over the same windows: median "
              f"{statistics.median(exk):.4f}, trimmed sd {trimmed_sd(exk):.4f}, "
              "se of the median "
              f"{MEDIAN_SE_FACTOR * trimmed_sd(exk) / math.sqrt(len(exk)):.4f}, "
              f"{sum(1 for x in exk if x > 0)} of {len(exk)} above zero, "
              f"{sum(1 for x in exk if x > 1.0)} above one")

    if args.emit:
        print("\n# facts.INDEX_TAIL_WINDOWS")
        for horizon in HORIZONS:
            print(f"    {horizon}: (")
            for start, end, k, n, *_ in emitted[horizon]:
                print(f'        ("{start}", "{end}", {k}, {n}),')
            print("    ),")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
