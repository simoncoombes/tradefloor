"""The tape: real S&P 500 and VIX closes 1990-01 .. 2025-07 and the two
replay windows (real index, real VIX, real names), frozen into
data/tape.json.gz so the instrument carries its own reference to a box.

    python tape.py build      rebuild data/tape.json.gz from the crash check's raw files

The raw files are programme/results/crashcheck/data/ (fetch_real.py, Yahoo)
and corpus/civ/real_closes.json (the 2020 names). `load()` never touches
them; the raw loaders here are the crash check's, moved, and the crash
check's episodes.real() and replay.real_window() now call them.
"""
import datetime as dt
import gzip
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
DESIGN = HERE.parents[1]
RAW = DESIGN / "programme" / "results" / "crashcheck" / "data"
CIV = DESIGN / "corpus" / "civ" / "real_closes.json"
TAPE = HERE / "data" / "tape.json.gz"
WINDOWS = {"gfc": ("2007-06-01", "2009-12-31"), "covid": ("2020-01-02", "2021-06-30")}


def _day(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).date()


def real_raw():
    """(S&P closes, VIX closes, timestamps) on the sessions both have."""
    d = json.load(open(RAW / "index-vix-1990-2025.json"))
    g = {_day(t): (t, c) for t, c in zip(d["GSPC"]["ts"], d["GSPC"]["close"])}
    v = {_day(t): c for t, c in zip(d["VIX"]["ts"], d["VIX"]["close"])}
    days = sorted(set(g) & set(v))
    return [g[x][1] for x in days], [v[x] for x in days], [g[x][0] for x in days]


def real_window_raw(name):
    lo, hi = (dt.date.fromisoformat(x) for x in WINDOWS[name])
    lvl, vix, ts = real_raw()
    idx = [i for i, t in enumerate(ts) if lo <= _day(t) <= hi]
    dates = [_day(ts[i]) for i in idx]
    if name == "gfc":
        D = json.load(open(RAW / "names-2007-2010.json"))["names"]
        px = {n: {_day(t): c for t, c in zip(v["ts"], v["close"])} for n, v in D.items() if v.get("ts")}
    else:
        D = json.load(open(CIV))
        px = {n: {_day(t): c for t, c in zip(v["ts"], v["adj"])} for n, v in D.items()}
    px = {n: p for n, p in px.items() if all(d in p for d in dates)}
    names = [[px[n][d] for d in dates] for n in sorted(px)]
    return {"dates": [str(d) for d in dates], "index": [lvl[i] for i in idx],
            "vix": [vix[i] for i in idx], "names": names, "name_symbols": sorted(px)}


def build():
    lvl, vix, ts = real_raw()
    T = {"source": "crashcheck/data/index-vix-1990-2025.json (Yahoo v8 daily), "
                   "names-2007-2010.json, corpus/civ/real_closes.json (adj)",
         "level": lvl, "vix": vix, "ts": ts,
         "dates": [str(_day(t)) for t in ts],
         "windows": {k: dict(real_window_raw(k), window=WINDOWS[k]) for k in WINDOWS}}
    TAPE.parent.mkdir(exist_ok=True)
    with gzip.open(TAPE, "wt") as f:
        json.dump(T, f)
    return T


_CACHE = {}


def load():
    if "t" not in _CACHE:
        with gzip.open(TAPE, "rt") as f:
            T = json.load(f)
        T["years"] = (T["ts"][-1] - T["ts"][0]) / (365.25 * 86400)
        for w in T["windows"].values():
            w["calm_end"] = next(i for i, v in enumerate(w["vix"]) if v > 30)
        _CACHE["t"] = T
    return _CACHE["t"]


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["build"]:
        T = build()
        print("tape", len(T["level"]), "sessions", T["dates"][0], "..", T["dates"][-1],
              {k: (len(w["dates"]), len(w["names"])) for k, w in T["windows"].items()}, TAPE.stat().st_size, "bytes")
    else:
        print(__doc__)
