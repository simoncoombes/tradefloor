"""The two-year band for `corr_persistence_acf1`, on the other shape rows' protocol.

Every other shape row's two-year band, `facts.REAL_MARKETS_UNIVERSAL_504`,
is built from `facts.UNIVERSAL_WINDOWS`: 32 of the certified forty, the
names trading before 1990, over 1986-07-09..2025-07-31. This tool rebuilds
that protocol from the tape and reads `corr_persistence_acf1` on it with the
function the simulated side runs, so the row's two-year band and the
model's two-year reading are one quantity.

THE PROTOCOL, step by step, and each step is the universal panel's:

    tape       Yahoo v8 daily bars through `tools/shadow/data.py`, adjusted
               close and reported volume, 1986-01-01..2025-08-01. Vendor
               data is not committed; the fetch is cached under
               tools/shadow/data/ and this tool prints its provenance.
    roster     the names of `data.TICKERS` with a bar before 1990-01-01:
               32 of the forty
    sessions   a name's bar counts when it reports a positive volume, and a
               session counts when all 32 names have one
    windows    consecutive 505-close blocks (504 returns) walked backward
               from the last common session, the remainder dropped at the
               start: 19 windows, labelled first close..last close
    crisis     a window holding 1987-10-19, 2008-10-15 or 2020-03-16 is
               dropped, which leaves 16
    estimator  `facts.panel_statistics`, the function `facts.measure` runs
               on a simulated market's daily bars. For this row it is the
               mean pairwise correlation on consecutive 21-return
               sub-windows, then the lag-1 autocorrelation of that series
    band       the fixed rule, median +/- t(16) * trimmed sd, each edge
               rounded outward (`facts.band_from_windows_fixed`)

THE CHECKS. The thirteen other shape rows are read on the same windows and
compared with the readings `facts.UNIVERSAL_WINDOWS` records, which is the
test that this run is on the tape and the windows those bands were built
from. Yahoo revises adjusted closes, so a fresh pull agrees to about 1e-4
rather than exactly: the pull of 2026-10-06 differs by at most 5.8e-5 on
any row of any window.

Two more readings are printed beside the band and do not change it:

  - THE SIMULATED SIDE'S WINDOW LENGTH. A 504-session run gives 504 closes,
    503 returns and 23 sub-windows; a 505-close tape window gives 504
    returns and 24. Every shape row's two-year band carries that one-return
    difference. Here it is measured twice: each tape window cut to its
    FIRST 504 closes, which keeps the sub-window boundaries and drops the
    twenty-fourth sub-window, and to its LAST 504 closes, which moves every
    boundary by one session. The second shows how much of the difference is
    where the 21-session boundaries fall rather than how many there are.
  - THE OLD TWO-YEAR BAND'S WINDOWS. `facts.REAL_PERSISTENCE_WINDOWS_504`
    is the forty-name 2015-2025 table behind the decade band
    `facts.REAL_MARKETS_504`. The forty names are walked the same way and
    read with the same function, to show whether that table is the same
    estimator on a different window set or a different estimator.

Usage:
    python tools/calibration/corr_persistence_504_band.py
    python tools/calibration/corr_persistence_504_band.py --out results.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "shadow"))
sys.path.insert(0, os.path.join(HERE, "..", "..", "python"))

ROW = "corr_persistence_acf1"
#: The span `UNIVERSAL_WINDOWS` was pulled over.
SPAN = ("1986-01-01", "2025-08-01")
#: A name trading before this date belongs to the 32-name roster.
ROSTER_BEFORE = "1990-01-01"
#: Closes per tape window: 504 returns, the universal panel's 504-bar window.
WINDOW_CLOSES = 505
#: The simulated side's closes over a 504-session run.
SIM_CLOSES = 504
HORIZON = 504


class _Instrument:
    """The two attributes `facts.panel_statistics` reads off a roster entry."""

    def __init__(self, sector: str) -> None:
        self.sector = sector
        self.shares_outstanding = 1.0


def _digest(rows: list) -> str:
    return hashlib.sha256(
        json.dumps(rows, separators=(",", ":")).encode()).hexdigest()


def tape(data, tickers):
    """`{ticker: [(date, close, volume)]}` on the common sessions, and them."""
    fetched = {t: data.fetch(t, *SPAN) for t in tickers}
    rows = {t: [(r[0], r[1], r[2]) for r in f["rows"] if r[2] and r[2] > 0]
            for t, f in fetched.items()}
    common = set.intersection(*(set(r[0] for r in v) for v in rows.values()))
    dates = sorted(common)
    series = {t: [r for r in v if r[0] in common] for t, v in rows.items()}
    provenance = {
        t: {"url": f["url"], "fetched": f["fetched"], "bars": len(f["rows"]),
            "first": f["rows"][0][0], "last": f["rows"][-1][0],
            "sha256": _digest(f["rows"])}
        for t, f in fetched.items()}
    return series, dates, provenance


def walked(n_dates: int, length: int) -> list[int]:
    """Window starts, oldest first: blocks of `length` walked back from the end."""
    starts, end = [], n_dates
    while end - length >= 0:
        starts.append(end - length)
        end -= length
    return list(reversed(starts))


def read(facts, pa, series, tickers, sectors, start, length, rows):
    """`rows` of `facts.panel_statistics` on closes `start .. start+length`."""
    ids, days, closes, volumes = [], [], [], []
    for i, t in enumerate(tickers):
        for d, (_, close, volume) in enumerate(series[t][start:start + length]):
            ids.append(i)
            days.append(d)
            closes.append(float(close))
            volumes.append(float(volume))
    table = pa.table({"instrument_id": ids, "day": days, "close": closes,
                      "volume": volumes})
    p = facts.panel_statistics(
        table, [_Instrument(sectors[t]) for t in tickers], min_observations=30)
    return {k: p.get(k) for k in rows} | {
        "returns": p["dependence_observations"]}


def band(facts, values):
    n = len(values)
    t = facts.BAND_RULE_FIXED_MULTIPLIER[n]
    centre = statistics.median(values)
    scale = facts.trimmed_sd(values)
    low, high = facts.band_from_windows_fixed(ROW, values, t)
    return {"n": n, "multiplier": t, "median": centre, "trimmed_sd": scale,
            "unrounded": [centre - t * scale, centre + t * scale],
            "band": [low, high]}


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Derive corr_persistence_acf1's two-year band on the "
                    "universal panel's protocol, with the simulated side's "
                    "estimator.")
    ap.add_argument("--out", help="write the derivation to this JSON file")
    ap.add_argument("--skip-decade", action="store_true",
                    help="skip the forty-name 2013-2025 check of "
                         "facts.REAL_PERSISTENCE_WINDOWS_504")
    args = ap.parse_args()

    import pyarrow as pa  # noqa: E402
    import data  # noqa: E402
    from tradefloor import facts  # noqa: E402

    sectors = {t: data.ENGINE_SECTOR[data.PANEL_SECTOR[t]]
               for t in data.TICKERS}
    first_bar = {t: data.fetch(t, *SPAN)["rows"][0][0] for t in data.TICKERS}
    roster = [t for t in data.TICKERS if first_bar[t] < ROSTER_BEFORE]
    shape = list(facts.SHAPE)
    recorded = facts.UNIVERSAL_WINDOWS
    out: dict = {
        "row": ROW,
        "horizon_days": HORIZON,
        "protocol": {
            "roster": f"the {len(roster)} names of tools/shadow/data.py "
                      f"TICKERS with a bar before {ROSTER_BEFORE}",
            "tape": f"Yahoo v8 chart API daily bars, {SPAN[0]}..{SPAN[1]}, "
                    "adjusted close and reported volume, through "
                    "tools/shadow/data.py",
            "sessions": "a name's bar counts when its volume is positive; a "
                        "session counts when every roster name has one",
            "windows": f"consecutive {WINDOW_CLOSES}-close blocks walked "
                       "backward from the last common session, remainder "
                       "dropped at the start",
            "crisis": "a window holding one of "
                      f"{list(recorded['crisis_dates'])} is dropped",
            "estimator": "facts.panel_statistics, the function facts.measure "
                         "runs on a simulated run's daily bars",
            "band": "facts.band_from_windows_fixed at "
                    "facts.BAND_RULE_FIXED_MULTIPLIER[n]",
        },
        "roster": roster,
    }

    series, dates, provenance = tape(data, roster)
    out["provenance"] = provenance
    out["common_sessions"] = {"count": len(dates), "first": dates[0],
                              "last": dates[-1]}
    print(f"{ROW} at {HORIZON} sessions: the universal panel's protocol")
    print(f"  roster: {len(roster)} names: {' '.join(roster)}")
    print(f"  common sessions: {len(dates)}, {dates[0]}..{dates[-1]} "
          f"(UNIVERSAL_WINDOWS records {recorded['common_bars']}, "
          f"{recorded['tape'][0]}..{recorded['tape'][1]})")

    starts = walked(len(dates), WINDOW_CLOSES)
    labels = [f"{dates[s]}..{dates[s + WINDOW_CLOSES - 1]}" for s in starts]
    same_labels = labels == list(recorded["windows"][HORIZON])
    print(f"  windows: {len(labels)} of {WINDOW_CLOSES} closes; boundaries "
          f"{'IDENTICAL to' if same_labels else 'DIFFERENT from'} "
          f"UNIVERSAL_WINDOWS[{HORIZON}]")

    windows = []
    for s, label in zip(starts, labels):
        full = read(facts, pa, series, roster, sectors, s, WINDOW_CLOSES,
                    shape)
        # The simulated side's length, 504 closes and 503 returns, cut two
        # ways: the first 504 closes keep every sub-window boundary, and the
        # last 504 move each one by a session.
        first = read(facts, pa, series, roster, sectors, s, SIM_CLOSES, [ROW])
        last = read(facts, pa, series, roster, sectors, s + 1, SIM_CLOSES,
                    [ROW])
        windows.append({
            "window": label,
            "crisis": facts.universal_window_is_crisis(label),
            "returns": full["returns"],
            "sub_windows": full["returns"] // facts.CORR_PERSISTENCE_WINDOW,
            "values": {k: full[k] for k in shape},
            "sim_length": {
                "returns": first["returns"],
                "sub_windows": (first["returns"]
                                // facts.CORR_PERSISTENCE_WINDOW),
                "first_504_closes": first[ROW],
                "last_504_closes": last[ROW]},
        })

    print()
    print("  the thirteen other shape rows against the recorded readings "
          "(max abs difference over the 19 windows):")
    reproduction = {}
    for k in shape:
        have = recorded["values"][HORIZON][k]
        mine = [w["values"][k] for w in windows]
        worst = max(abs(a - b) for a, b in zip(mine, have))
        reproduction[k] = worst
        print(f"    {k:<24} {worst:.2e}")
    out["windows_identical_to_recorded"] = same_labels
    out["reproduction_max_abs_difference"] = reproduction

    print()
    print(f"  {ROW} per window: recorded | this pull, 505 closes | first "
          f"504 closes | last 504 closes")
    for w, have in zip(windows, recorded["values"][HORIZON][ROW]):
        flag = "  crisis, dropped" if w["crisis"] else ""
        sim = w["sim_length"]
        print(f"    {w['window']}  {have:+.6f} | {w['values'][ROW]:+.6f} "
              f"({w['sub_windows']} sub-windows) | "
              f"{sim['first_504_closes']:+.6f} ({sim['sub_windows']}) | "
              f"{sim['last_504_closes']:+.6f} ({sim['sub_windows']}){flag}")
    out["windows"] = windows

    kept = [w for w in windows if not w["crisis"]]
    derived = {
        "recorded_readings": band(
            facts, list(facts.universal_windows(ROW, HORIZON))),
        "this_pull": band(facts, [w["values"][ROW] for w in kept]),
        "this_pull_first_504_closes": band(
            facts, [w["sim_length"]["first_504_closes"] for w in kept]),
        "this_pull_last_504_closes": band(
            facts, [w["sim_length"]["last_504_closes"] for w in kept]),
    }
    out["bands"] = derived
    out["band"] = derived["recorded_readings"]["band"]
    out["band_source"] = (
        "facts.UNIVERSAL_WINDOWS[504]['corr_persistence_acf1'], the readings "
        "the other thirteen shape rows' two-year bands share a pull with; "
        "this pull reproduces them to the difference printed beside")
    print()
    for name, b in derived.items():
        print(f"  band from {name.replace('_', ' ')}: n = {b['n']}, "
              f"t = {b['multiplier']}, median {b['median']:.6f}, "
              f"trimmed sd {b['trimmed_sd']:.6f}, unrounded "
              f"({b['unrounded'][0]:.6f}, {b['unrounded'][1]:.6f}) -> "
              f"({b['band'][0]:.2f}, {b['band'][1]:.2f})")
    null = facts.persistence_null(facts.sub_window_count(HORIZON))
    out["null_at_simulated_sub_windows"] = {
        "sub_windows": facts.sub_window_count(HORIZON), "median": null}
    print(f"  the estimator's own null at the simulated side's "
          f"{facts.sub_window_count(HORIZON)} sub-windows "
          f"(facts.persistence_null): {null:+.6f}")
    shipped = facts.REAL_MARKETS_UNIVERSAL_504.get(ROW)
    print(f"  facts.REAL_MARKETS_UNIVERSAL_504[{ROW!r}] = {shipped}")
    out["universal_504_entry"] = list(shipped) if shipped else None

    if not args.skip_decade:
        out["decade_check"] = decade_check(facts, pa, data, sectors)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1)
            f.write("\n")
        print(f"wrote {args.out}")
    return 0


def decade_check(facts, pa, data, sectors) -> dict:
    """Read the forty names on `REAL_MARKETS_WINDOWS_504`'s six windows."""
    print()
    print("  the old two-year band's windows: the forty names, walked the "
          "same way")
    series, dates, _ = tape(data, list(data.TICKERS))
    starts = walked(len(dates), WINDOW_CLOSES)[-6:]
    table = facts.REAL_MARKETS_WINDOWS_504
    old = facts.REAL_PERSISTENCE_WINDOWS_504
    rows = list(facts.SHAPE)
    readings = []
    for s in starts:
        label = f"{dates[s]}..{dates[s + WINDOW_CLOSES - 1]}"
        values = read(facts, pa, series, list(data.TICKERS), sectors, s,
                      WINDOW_CLOSES, rows)
        readings.append({"window": label, "values": values})
    same = [r["window"] for r in readings] == list(table["windows"])
    print(f"    windows {'IDENTICAL to' if same else 'DIFFERENT from'} "
          f"REAL_MARKETS_WINDOWS_504")
    worst = {}
    for k in rows:
        if k == ROW:
            continue
        have = table["values"][k]
        worst[k] = max(abs(r["values"][k] - h)
                       for r, h in zip(readings, have))
    print(f"    the thirteen other rows: worst difference "
          f"{max(worst.values()):.2e}")
    persistence = {}
    for r in readings:
        recorded = None
        if r["window"] in old["windows"]:
            recorded = old["values"][ROW][old["windows"].index(r["window"])]
        persistence[r["window"]] = {"this_pull": r["values"][ROW],
                                    "REAL_PERSISTENCE_WINDOWS_504": recorded}
        shown = "not in that table" if recorded is None else f"{recorded:+.6f}"
        print(f"    {r['window']}  {r['values'][ROW]:+.6f}  "
              f"REAL_PERSISTENCE_WINDOWS_504: {shown}")
    return {"windows_identical": same, "other_rows_max_abs_difference": worst,
            ROW: persistence}


if __name__ == "__main__":
    sys.exit(main())
