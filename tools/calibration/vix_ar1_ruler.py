"""The VIX persistence ruler, derived from the tape at the horizon it grades.

The model's `vix_ar1_debiased` row is a PER-RUN reading: one seed's daily
VIX levels over a 252-day run, the lag-one autocorrelation about that run's
own mean, the median across seeds, then the Marriott-Pope / Kendall first
correction at the run length. For months it was compared against **0.976**,
which is the WHOLE-SPAN autocorrelation of ^VIX -- one series of 8,960 bars,
correctly documented as whole-span in the design repository and compared
against 252-day rows anyway. Two different quantities, and the model's is
the smaller one, so every identity arm on the record reads as SHORT of the
real VIX when on a like-for-like ruler it is more persistent than real.

This tool derives the like-for-like figure. It cuts the same tape into
consecutive non-overlapping blocks of `--window` closes -- non-overlapping
because the model's runs are independent paths, front-anchored at the tape's
first bar because that is the one anchor nobody chooses -- and runs
`tradefloor.facts.median_level_ar1` over them: THE SAME FUNCTION the model's
row goes through, applied to different data. That property is what
`tests/test_vix_ar1_ruler.py` asserts; a tool with its own copy of the
estimator would reproduce the defect it exists to remove.

The readings it prints are recorded in `facts.REAL_VIX_AR1_WINDOWS`, from
which `facts.REAL_VIX_AR1` is derived at import, because the tape is vendor
data and is not committed. Run this to re-derive them; it reports the
largest difference against what is recorded.

Usage:  python tools/calibration/vix_ar1_ruler.py [--window 252 504]
                                                  [--boot-draws 20000]
"""
import argparse
import math
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "shadow"))
import data  # noqa: E402

import tradefloor.facts as facts  # noqa: E402

#: The pinned span, which is the one `facts.REAL_VIX_AR1_PROVENANCE` states.
#: Different dates fetch a different tape and derive a different ruler; the
#: recorded windows are this span's.
SYMBOL, START, END = "^VIX", "1990-01-02", "2025-07-31"

#: The bootstrap that puts an error bar on the MEDIAN across windows, which
#: is the statistic taken -- the standard error of a mean would understate it
#: by about a fifth. Seeded, so the residual in `facts` is reproducible.
BOOT_SEED = 20260906


def closes(symbol, start, end):
    """Daily closes in date order, with the fetch provenance."""
    d = data.fetch(symbol, start, end)
    rows = [r for r in d["rows"] if r[1] is not None and r[1] > 0]
    rows.sort()
    return ([r[0] for r in rows], [float(r[1]) for r in rows],
            d["url"], d["fetched"])


def blocks(levels, window, *, anchor="front"):
    """Consecutive non-overlapping blocks of `window` closes.

    The incomplete tail is dropped rather than part-filled: a shorter block
    would be debiased at a length it does not have, which is the error one
    level down from the one this tool exists for.
    """
    n = len(levels) // window
    if anchor == "front":
        return [levels[i * window:(i + 1) * window] for i in range(n)]
    return [levels[len(levels) - (i + 1) * window:len(levels) - i * window]
            for i in range(n)][::-1]


def bootstrap_se_of_median(readings, draws, seed):
    rng = random.Random(seed)
    n = len(readings)
    medians = [statistics.median([readings[rng.randrange(n)] for _ in range(n)])
               for _ in range(draws)]
    return statistics.stdev(medians)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, nargs="+", default=[252, 504],
                    help="the horizon(s) to derive the ruler at, in sessions")
    ap.add_argument("--boot-draws", type=int, default=20000)
    ap.add_argument("--start", default=START)
    ap.add_argument("--end", default=END)
    args = ap.parse_args()

    dates, levels, url, fetched = closes(SYMBOL, args.start, args.end)
    print("%s daily close, %s to %s, %d bars" % (SYMBOL, dates[0], dates[-1],
                                                 len(levels)))
    print("  url %s  fetched %s" % (url, fetched))

    whole = facts.level_ar1(levels)
    print()
    print("NOT THE RULER -- the whole span as one series:")
    print("  raw %.6f, debiased at n=%d %.6f" % (whole, len(levels),
                                                 facts.debias_ar1(whole, len(levels))))
    print("  this is the 0.976 on the record. The model has no run of this "
          "length and")
    print("  concatenating its seeds would join paths that are not one "
          "series.")

    for window in args.window:
        front = blocks(levels, window)
        if not front:
            print("\nwindow %d: the tape is shorter than one window" % window)
            continue
        raws = [facts.level_ar1(b) for b in front]
        ruler = facts.median_level_ar1(front, length=window)
        end_ruler = facts.median_level_ar1(blocks(levels, window, anchor="end"),
                                           length=window)
        boot = bootstrap_se_of_median(raws, args.boot_draws, BOOT_SEED)
        last = dates[len(front) * window - 1]
        dropped = len(levels) - len(front) * window
        print()
        print("window %d sessions: %d non-overlapping blocks, %s to %s, "
              "%d bars dropped" % (window, len(front), dates[0], last, dropped))
        print("  raw per-window median %.6f, mean %.6f, sd %.6f, "
              "min %.6f, max %.6f"
              % (statistics.median(raws), statistics.fmean(raws),
                 statistics.stdev(raws), min(raws), max(raws)))
        print("  se of the mean %.6f; bootstrap se of the MEDIAN %.6f "
              "(%d resamples, seed %d)"
              % (statistics.stdev(raws) / math.sqrt(len(raws)), boot,
                 args.boot_draws, BOOT_SEED))
        print("  RULER, debiased at %d: %.6f" % (window, ruler))
        moved = abs(end_ruler - ruler)
        print("  end-anchored cut: %.6f, moving the ruler by %.6f%s"
              % (end_ruler, moved,
                 " -- MORE than its own sampling error" if moved > boot
                 else ""))
        recorded = facts.REAL_VIX_AR1_WINDOWS.get(window)
        if recorded is None:
            print("  NOT RECORDED in facts.REAL_VIX_AR1_WINDOWS; paste the "
                  "readings below to add this horizon")
        elif len(recorded) != len(raws):
            print("  RECORDED WINDOWS DISAGREE: %d recorded against %d "
                  "derived" % (len(recorded), len(raws)))
        else:
            worst = max(abs(a - b) for a, b in zip(recorded, raws))
            print("  against facts.REAL_VIX_AR1_WINDOWS[%d]: largest "
                  "difference %.2e; facts.REAL_VIX_AR1[%d] = %.6f"
                  % (window, worst, window, facts.REAL_VIX_AR1[window]))
        print("  %d: (" % window)
        line = "     "
        for value in raws:
            piece = " %.6f," % value
            if len(line) + len(piece) > 74:
                print(line)
                line = "     "
            line += piece
        print(line)
        print("  ),")


if __name__ == "__main__":
    main()
