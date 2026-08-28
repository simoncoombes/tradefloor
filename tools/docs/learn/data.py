"""Generate the learning site's chart data from the repository's own files.

The handoff shipped `pt-data.js` as a hand-decoded snapshot and named it the
one thing to fix in implementation: "if the goldens change, the charts
silently lie". This is that fix. Every number the pages plot is read here
from the file that owns it -

    rust/goldens/thirty-day-calm.json      the market player, the table
    rust/goldens/thirty-day-eventful.json  previews, the truth columns
    rust/goldens/orderbook.json            the 22-step order-book replay
    measurements/real-panel.json           the fourteen real bands
    measurements/seed-sd-504.json          the measured values
    docs/envelope.json                     the five gaps, the horizon
    examples/data/covid-2020-2021.json     the real 2020 macro path

- so a golden that moves either moves the chart with it or fails the build.

The goldens hold their floats as big-endian IEEE-754 in sixteen hex digits,
which is what makes them exact across the TypeScript, Rust and wasm builds
that all have to agree. Decoding is therefore lossless, and the rounding
applied below is a display decision, made once, here, rather than by hand in
a data file nobody diffs.

    python tools/docs/learn/data.py --check

compares what this produces against the vendored snapshot and reports every
value that differs, which is how the decoding was established in the first
place.
"""

from __future__ import annotations

import argparse
import decimal
import json
import pathlib
import struct
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
GOLDENS = ROOT / "rust" / "goldens"
MEASUREMENTS = ROOT / "measurements"


def f64(hex_or_none):
    """A golden's float. `null` stays None; everything else is exact."""
    if hex_or_none is None:
        return None
    return struct.unpack(">d", bytes.fromhex(hex_or_none))[0]


def plain(text):
    """Typography the site does not use.

    The goldens carry a step note or two written with an em dash. They are
    parity fixtures — the same bytes have to decode identically in
    TypeScript, Rust and wasm — so the fix belongs on the way out, next to
    the rounding, rather than in a file whose job is to not change.
    """
    if text is None:
        return None
    return text.replace(" \u2014 ", " - ").replace("\u2014", "-")


def r(value, places):
    """Round for display, the way the browser would have.

    Half-up, away from zero, because that is what `Number.prototype.toFixed`
    does and the snapshot this replaces was produced with it. Python's built
    in `round` is half-to-even, which disagrees on an exact half - three of
    the five hundred S&P closes, as it happens, which is exactly the kind of
    difference that is invisible until someone diffs two files and cannot
    explain it.
    """
    if value is None:
        return None
    quantum = decimal.Decimal(1).scaleb(-places)
    out = decimal.Decimal(value).quantize(quantum, rounding=decimal.ROUND_HALF_UP)
    return int(out) if places == 0 else float(out)


def load(path: pathlib.Path):
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


# ------------------------------------------------------------------- market

#: How many decimals each series carries into the page. Prices are money,
#: returns and mispricings are read at five places because the tables print
#: them there, and GARCH variance needs eight before it stops being zero.
PLACES = {"px": 2, "ret": 5, "mis": 5, "mom": 5, "gv": 8}


def market(name: str, path: pathlib.Path) -> dict:
    g = load(path)
    spec = g["spec"]

    names = []
    for company in g["initial"]:
        names.append({
            "t": company["ticker"],
            "s": company["sector"],
            "p0": r(f64(company["stock"]["price"]), 2),
            "eps": r(f64(company["eps"]), 2),
            "bvps": r(f64(company["bookValuePerShare"]), 2),
            "beta": r(f64(company["stock"]["beta"]), 3),
        })

    days = []
    for i, day in enumerate(g["days"]):
        econ = day["economy"]
        companies = day["companies"]
        days.append({
            "d": i,
            "vix": r(f64(econ["vix"]), 2),
            "ffr": r(f64(econ["federalFundsRate"]), 2),
            # Four places, not two: the eventful run moves the corporate
            # bond yield by fractions of a basis point and the chart plots
            # the move.
            "cby": r(f64(econ["corporateBondYield"]), 4),
            "infl": r(f64(econ["inflationRate"]), 4),
            "fg": r(f64(econ["fearGreedIndex"]), 1),
            "phase": econ["cyclePhase"],
            "mt": 1 if day["meetingHeld"] else 0,
            "pc": 1 if day["phaseChanged"] else 0,
            "px": [r(f64(c["price"]), PLACES["px"]) for c in companies],
            "ret": [r(f64(c["lastDailyReturn"]), PLACES["ret"]) for c in companies],
            "mis": [r(f64(c["mispricingS"]), PLACES["mis"]) for c in companies],
            "mom": [r(f64(c["mispricingMomentum"]), PLACES["mom"]) for c in companies],
            "gv": [r(f64(c["garchVariance"]), PLACES["gv"]) for c in companies],
        })

    return {
        "spec": {
            "name": spec["name"], "seed": spec["seed"], "tickSeed": spec["tickSeed"],
            "days": spec["days"], "ticksPerDay": spec["ticksPerDay"],
        },
        "names": names,
        "days": days,
    }


# --------------------------------------------------------------- order book

def order_book(steps: int = 22) -> list[dict]:
    """The replay on the front door.

    The golden runs seventy-five steps; the design walks the first
    twenty-two, which is where the ladder is built up and first crossed.
    """
    g = load(GOLDENS / "orderbook.json")
    out = []
    for step in g["steps"][:steps]:
        state = step["state"]
        result = step.get("result") or {}
        out.append({
            "i": step["step"],
            "note": plain(step["note"]),
            "op": _op(step["op"]),
            "fills": [_fill(x) for x in (result.get("fills") or [])],
            "bids": [_level(x) for x in (state.get("bids") or [])],
            "asks": [_level(x) for x in (state.get("asks") or [])],
            "bb": r(f64(state.get("bestBid")), 2),
            "ba": r(f64(state.get("bestAsk")), 2),
            "mid": r(f64(state.get("midPrice")), 2),
            "spr": r(f64(state.get("spread")), 2),
            "last": r(f64(state.get("lastPrice")), 2),
        })
    return out


def _op(op):
    """The operation a step performs, named as the ladder reads it."""
    if not op:
        return None
    return {
        "kind": op.get("op"),
        "side": op.get("side"),
        "price": r(f64(op.get("price")), 2),
        "qty": r(f64(op.get("quantity")), 0),
        "type": op.get("type"),
        "owner": op.get("ownerId"),
    }


def _fill(x):
    """A trade. The replay names the maker, because whose resting order was
    taken is the thing the step is illustrating."""
    return {
        "price": r(f64(x.get("price")), 2),
        "qty": r(f64(x.get("quantity")), 0),
        "maker": x.get("makerId"),
    }


def _level(x):
    """One resting order. `o` is its owner, which is what the replay labels."""
    return {
        "o": x.get("ownerId"),
        "p": r(f64(x.get("price")), 2),
        "rem": r(f64(x.get("remaining")), 0),
        "seq": x.get("sequence"),
    }


# ---------------------------------------------------------------- envelope

def envelope() -> dict:
    """The fourteen statistics, their real bands, and the five named gaps.

    One source, `docs/envelope.json`, which is what `pretium.envelope`
    publishes: it already carries each statistic's measured value, the band
    it was graded against and the verdict, so reading the bands from
    `measurements/real-panel.json` separately - as the handoff's provenance
    table suggested - would be a second copy that could disagree with the
    first. The measurement files remain the source *of* envelope.json; this
    page quotes the published envelope, which is what a reader can check.
    """
    env = load(ROOT / "docs" / "envelope.json")
    stats = []
    for key, stat in env["statistics"].items():
        stats.append({
            "k": key,
            "m": stat["measured"],
            "band": stat["band"],
            "in": stat["in_band"],
        })
    gaps = []
    for gap in env["gaps"]:
        gaps.append({
            "id": gap["id"],
            "summary": gap["summary"],
            "forbids": gap["forbids"],
            "stats": gap["statistics"],
            "beyond": gap["beyond_days"],
        })
    return {
        "preset": env["preset"],
        "horizon": env["certified_horizon_days"],
        "stats": stats,
        "gaps": gaps,
    }


def real_2020() -> dict:
    """The real macro path the scenario pages plot against.

    Kept to the four series the pages actually draw. The file carries a
    fifth, `hyg`, which no page uses; shipping it would add a hundred
    kilobytes to every page load to no end.
    """
    src = load(ROOT / "examples" / "data" / "covid-2020-2021.json")
    return {
        "about": src["about"],
        "source": src["source"],
        "retrieved": src["retrieved"],
        "dates": src["dates"],
        # Two decimals, which is the resolution the series are quoted at and
        # a third of the bytes of the raw closes.
        "vix": [r(v, 2) for v in src["vix"]],
        "aapl": [r(v, 2) for v in src["aapl"]],
        # The index is four figures before the point; one after it is the
        # same precision as two on a share price.
        "spx": [r(v, 1) for v in src["spx"]],
        # The ten-year yield is quoted to a tenth of a basis point.
        "tnx": [r(v, 3) for v in src["tnx"]],
    }


# -------------------------------------------------------------------- write

def build() -> dict:
    return {
        "market": {
            "calm": market("calm", GOLDENS / "thirty-day-calm.json"),
            "eventful": market("eventful", GOLDENS / "thirty-day-eventful.json"),
        },
        "real2020": real_2020(),
        "envelope": envelope(),
        "sectors": load(GOLDENS / "thirty-day-calm.json")["sectorKeys"],
        "book": order_book(),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="diff against the vendored snapshot instead of writing")
    args = ap.parse_args()

    generated = build()

    if args.check:
        snapshot = snapshot_data()
        problems = []
        for key in sorted(generated):
            if key not in snapshot:
                problems.append(f"{key}: absent from the snapshot")
                continue
            diff(generated[key], snapshot[key], key, problems)
        extra = sorted(set(snapshot) - set(generated))
        for key in extra:
            problems.append(f"{key}: in the snapshot, not yet generated")
        if problems:
            print(f"{len(problems)} difference(s) against the snapshot:")
            for p in problems[:60]:
                print("  " + p)
            sys.exit(1)
        print("generated data matches the vendored snapshot")
        return

    print(json.dumps(generated)[:200])


def snapshot_data() -> dict:
    """The hand-decoded `pt-data.js`, read back as data.

    Evaluated rather than parsed: it is a JavaScript module, not JSON, and
    the point of reading it at all is to compare against exactly what the
    browser would have got.
    """
    import subprocess
    script = (
        "const fs=require('fs'),vm=require('vm');"
        "const ctx={};ctx.window=ctx;vm.createContext(ctx);"
        "vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),ctx);"
        "process.stdout.write(JSON.stringify(ctx.PT));"
    )
    out = subprocess.run(
        ["node", "-e", script, str(HERE / "handoff" / "pt-data.js")],
        capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)


def diff(a, b, path, problems, depth=0):
    if len(problems) > 200:
        return
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                problems.append(f"{path}.{k}: only in the snapshot")
            elif k not in b:
                problems.append(f"{path}.{k}: only generated")
            else:
                diff(a[k], b[k], f"{path}.{k}", problems, depth + 1)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            problems.append(f"{path}: {len(a)} generated, {len(b)} in the snapshot")
            return
        for i, (x, y) in enumerate(zip(a, b)):
            diff(x, y, f"{path}[{i}]", problems, depth + 1)
    elif a != b:
        problems.append(f"{path}: {a!r} generated, {b!r} in the snapshot")


if __name__ == "__main__":
    main()
