"""The tape side of `crisis_sector_dispersion`, derived from ^VIX and 32 names.

The row, from `programme/crisis-dispersion-row-design-2026-09-22.md`
section 2: inside a window, how much more the hardest-hit sector moves than
the typical sector once the volatility index is above the engine's own
crisis threshold.

    crisis sessions = sessions with ^VIX above `crisis_vix_threshold` (30.88)
    per name        = sd(daily log return on crisis sessions)
                      / sd(on the rest)
    per sector      = the median ratio over its names
    the row         = max over sectors of (sector ratio / their median)
    under thirty crisis sessions the window is ABSENT

THE ESTIMATOR IS NOT IN THIS FILE. It is `facts.crisis_dispersion`, the
same function `facts.crisis_statistics` runs on the model, so the ruler and
the row cannot drift into two quantities the way the VIX AR1 ruler and its
row did (`facts.REAL_VIX_AR1_WINDOWS`'s neighbours record that). This tool
reads the tape, cuts the windows and prints; the arithmetic is the
library's.

THE SERIES. ^VIX daily closes and the adjusted closes of the reference
roster's names with the index's full history, both through
`tools/shadow/data.py` (Yahoo v8 chart API; vendor data is not committed,
so this tool re-derives and `facts.py` records what it derived). The names
are pulled over 1987-06-01..2025-07-31 and the ones whose bar count equals
^GSPC's over the same span are kept -- 32 of the forty, which is the same
32-name set `facts.UNIVERSAL_WINDOWS` is measured on -- with
`data.PANEL_SECTOR` mapped through `data.ENGINE_SECTOR` onto the engine's
own sector keys, so a sector means the same thing on both sides.

THE WINDOWS START WHERE THE VIX STARTS, 1990-01-02, and that is said here
rather than left to be inferred. ^VIX does not exist before it: a fetch
with `period1` at 1980 returns a first bar of 1990-01-02. The names reach
back to 1987-06 and the index to 1987-06, but a session with no VIX cannot
be called a crisis session or a calm one, so the cut is the VIX's and the
2,900-odd earlier sessions are dropped. The windows are then front-anchored
non-overlapping blocks of 252 and 504 aligned closes with the incomplete
tail dropped, which is `REAL_VIX_AR1_WINDOWS`'s cut and the argument there
is the argument here: the tape's own start is the one anchor nobody chose.

THE BAND is the `fixed` rule the fear rows use, `median +/- t(n) *
trimmed_sd` with each edge rounded outward, at the t solved for the READABLE
window count -- not the block count, because a window with no crisis in it
supplies no reading to take a median of.

Usage:  python tools/calibration/crisis_dispersion_row.py
"""
import math
import os
import statistics
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "shadow"))
sys.path.insert(0, os.path.join(HERE, "..", "..", "python"))
import data  # noqa: E402

from tradefloor.facts import (  # noqa: E402
    BAND_RULE_FIXED_MULTIPLIER, BAND_RULE_FIXED_TOLERANCE,
    CRISIS_DISPERSION_MIN_SESSIONS, CRISIS_DISPERSION_ROW,
    CRISIS_VIX_THRESHOLD, band_from_windows_fixed, crisis_dispersion,
    trimmed_sd,
)

#: The span the names and the index are pulled over. The names are cut to
#: the VIX's sessions below; this is only how much history a name has to
#: have to count as a full-history name.
SPAN = ("1987-06-01", "2025-07-31")
#: ^VIX's own first bar, and the first session of every window.
VIX_SPAN = ("1990-01-02", "2025-07-31")
HORIZONS = (252, 504)


def tape():
    """The aligned tape: dates, the VIX level, a close matrix and the sectors."""
    index = data.fetch(data.INDEX, *SPAN)["rows"]
    names = {t: data.fetch(t, *SPAN)["rows"] for t in data.TICKERS}
    # A name with the index's own bar count has the whole span; the rest
    # listed by ticker rather than counted, because which names are missing
    # is the roster the row is measured on.
    full = sorted(t for t, rows in names.items() if len(rows) == len(index))
    vix = data.fetch(data.VIX, *VIX_SPAN)["rows"]
    level = {r[0]: r[1] for r in vix}
    closes = {t: {r[0]: r[1] for r in names[t]} for t in full}
    dates = [r[0] for r in vix if all(r[0] in closes[t] for t in full)]
    sectors = {t: data.ENGINE_SECTOR[data.PANEL_SECTOR[t]] for t in full}
    return {
        "names": full,
        "dates": dates,
        "vix": np.array([level[d] for d in dates], float),
        "closes": np.array([[closes[t][d] for d in dates] for t in full], float),
        "sectors": sectors,
        "index_bars": len(index),
        "vix_bars": len(vix),
    }


def windows(t, horizon):
    """Every front-anchored block of `horizon` closes, readable or not."""
    dates, names = t["dates"], t["names"]
    out = []
    for start in range(0, len(dates) - horizon + 1, horizon):
        stop = start + horizon
        block = t["closes"][:, start:stop]
        returns = {name: list(np.diff(np.log(block[i])))
                   for i, name in enumerate(names)}
        vix = list(t["vix"][start + 1:stop])
        crisis = sum(1 for v in vix if v > CRISIS_VIX_THRESHOLD)
        try:
            value = crisis_dispersion(returns, vix, t["sectors"])
        except Exception as exc:                      # ValidationError
            value, why = None, str(exc)
        else:
            why = None
        out.append({"start": dates[start], "end": dates[stop - 1],
                    "crisis": crisis, "value": value, "why": why})
    return out


def main():
    t = tape()
    print(f"{CRISIS_DISPERSION_ROW}: the tape")
    print(f"  ^GSPC {SPAN[0]}..{SPAN[1]}, {t['index_bars']} bars; "
          f"{len(t['names'])} of {len(data.TICKERS)} names carry all of it")
    print(f"  names: {' '.join(t['names'])}")
    counts = {}
    for name in t["names"]:
        counts[t["sectors"][name]] = counts.get(t["sectors"][name], 0) + 1
    print("  sectors: " + ", ".join(f"{s} {n}" for s, n in sorted(counts.items())))
    print(f"  ^VIX {VIX_SPAN[0]}..{t['dates'][-1]}, {t['vix_bars']} bars; "
          f"{len(t['dates'])} sessions carry a VIX and all {len(t['names'])} "
          f"names, and the windows start at the VIX's first bar")
    print(f"  crisis sessions (VIX above {CRISIS_VIX_THRESHOLD}): "
          f"{int((t['vix'] > CRISIS_VIX_THRESHOLD).sum())} of {len(t['dates'])}")
    print()
    recorded = {}
    for horizon in HORIZONS:
        blocks = windows(t, horizon)
        readable = [b for b in blocks if b["value"] is not None]
        print(f"  {horizon}-close windows: {len(blocks)} blocks, "
              f"{len(readable)} readable at {CRISIS_DISPERSION_MIN_SESSIONS}+ "
              f"crisis sessions")
        for b in blocks:
            if b["value"] is None:
                print(f"    {b['start']}..{b['end']}  crisis {b['crisis']:>3}  "
                      f"ABSENT")
            else:
                print(f"    {b['start']}..{b['end']}  crisis {b['crisis']:>3}  "
                      f"{b['value']:.6f}")
        values = [b["value"] for b in readable]
        recorded[horizon] = values
        n = len(values)
        multiplier = BAND_RULE_FIXED_MULTIPLIER.get(n)
        centre = statistics.median(values)
        scale = trimmed_sd(values)
        print(f"    n = {n}  centre (median) {centre:.6f}  "
              f"trimmed sd {scale:.6f}")
        if multiplier is None:
            print(f"    NO BAND: the fixed rule's multiplier at {n} windows "
                  f"is not solved; solve it with "
                  f"facts.band_rule_fixed_false_alarm and record it")
        else:
            low, high = band_from_windows_fixed(
                CRISIS_DISPERSION_ROW, values, multiplier)
            print(f"    band: median +/- t({n}) * trimmed sd, t = "
                  f"{multiplier}, rate {BAND_RULE_FIXED_TOLERANCE}, "
                  f"edges rounded outward")
            print(f"    unrounded ({centre - multiplier * scale:.6f}, "
                  f"{centre + multiplier * scale:.6f})  ->  ({low:.2f}, {high:.2f})")
        print()
    print("  the readings, as facts.REAL_CRISIS_DISPERSION_WINDOWS records them:")
    for horizon, values in recorded.items():
        print(f"    {horizon}: (" + ", ".join(f"{v:.6f}" for v in values) + ",)")
    try:
        from tradefloor.facts import REAL_CRISIS_DISPERSION_WINDOWS
    except ImportError:
        return
    print()
    for horizon, values in recorded.items():
        have = REAL_CRISIS_DISPERSION_WINDOWS.get(horizon)
        if have is None:
            print(f"    {horizon}: NOT RECORDED in facts")
            continue
        worst = max(abs(a - b) for a, b in zip(values, have)) if (
            len(have) == len(values)) else float("inf")
        print(f"    {horizon}: facts records {len(have)} windows, this run "
              f"derives {len(values)}, worst disagreement {worst:.2e}")


if __name__ == "__main__":
    main()
