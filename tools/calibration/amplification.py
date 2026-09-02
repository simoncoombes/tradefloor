"""Amplification: the same draws under two presets, differenced.

    python tools/calibration/amplification.py --presets pt-v14 pt-v16 \\
        --seed 42 --days 40 --window 30 34 --workers 16 --out out/amp

Attribute a target in every draw of a window (``tradefloor.noise.attribute``)
under preset A and again under preset B, at the same seed and therefore the
same addresses, and difference the two. Where the difference is large the
second preset amplifies (or damps) what the noise does; where the effects
correlate across presets at a site, the mechanism reading that draw is the
same in both and only its gain moved.

The target is the equal-weight index log return over the window: the mean
across names of log(close on the window's last day / close on the day
before the window). Order flow is held at zero (a flat agent), which is
what a preset comparison wants and what real data has.

Both attributions come with the caveats ``attribute`` computed; the
report repeats them. What this tool adds on top is only arithmetic:
differences, ratios of summed absolute effects per site, and the
correlation of effects across presets at the same address. No number here
is a measurement of a real market.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import tradefloor as tf
from tradefloor import noise
from tradefloor.counterfactual import World


class Flat:
    """An agent that sends no orders. Order flow held at zero."""

    def act(self, obs):
        return {}

    def state(self):
        return {}


def index_return(first: int, last: int):
    """Mean log return of the roster from the close before ``first`` to the
    close of ``last``, read off the world's trace."""

    def target(world):
        before = [row for row in world.trace if row["day"] == first - 1][-1]
        after = [row for row in world.trace if row["day"] == last][-1]
        pairs = zip(before["prices"], after["prices"])
        return sum(math.log(b / a) for a, b in pairs) / len(before["prices"])

    target.__name__ = f"index_log_return[{first - 1}->{last}]"
    return target


def build(preset: str, seed: int, roster_seed: int, names: int,
          first: int) -> World:
    universe = tf.Universe.random(names, seed=roster_seed)
    world = World(seed=seed, universe=universe, agent=Flat(), model=preset,
                  steps_per_day=1, ticks_per_step=390, label=preset)
    world.run(first)
    return world


def shard(args) -> list[dict]:
    (preset, seed, roster_seed, names, first, last, horizon, streams,
     granularity, delta, i, n) = args
    world = build(preset, seed, roster_seed, names, first)
    report = noise.attribute(world, (first, last), index_return(first, last),
                             granularity, streams=streams, delta=delta,
                             horizon=horizon, shard=(i, n))
    return report.rows


def attribute_preset(preset: str, a: argparse.Namespace) -> dict:
    first, last = a.window
    world = build(preset, a.seed, a.roster_seed, a.names, first)
    digest = world.digest()
    fingerprint = world.engine.model_fingerprint
    # One shard for the caveats and the control, then the rows in parallel.
    probe = noise.attribute(world, (first, last), index_return(first, last),
                            a.granularity, streams=a.streams, delta=a.delta,
                            horizon=a.days - 1, shard=(0, 10 ** 9))
    jobs = [(preset, a.seed, a.roster_seed, a.names, first, last, a.days - 1,
             a.streams, a.granularity, a.delta, i, a.workers)
            for i in range(a.workers)]
    rows: list[dict] = []
    started = time.time()
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        for part in pool.map(shard, jobs):
            rows.extend(part)
    rows.sort(key=lambda r: (r["stream"], r["day"], r["site"], r["tag"],
                             r["index"], r["perturbation"]))
    # The probe is one row, so every caveat whose text counts rows counts
    # one. Those are dropped by identity rather than by matching their
    # words, and restated over the merged rows: a filter on the strings
    # published "1 event uniforms were forced to each end" above a table
    # holding 18, and left the same miscount on the non-event uniforms.
    # The interaction line is not among them, because it needs an arm with
    # every patch installed and no shard has one.
    kept = [c for c in probe.caveats
            if c not in probe.plan_caveats and "shard" not in c]
    kept += noise.row_caveats(rows, target=probe.target, last=last,
                              horizon=a.days - 1,
                              event_streams=[s for s in a.streams
                                             if s in noise.EVENT_STREAMS])
    kept.append(
        "the rows are single-draw finite differences and are not additive; "
        "this report does not install them together, so it states no total "
        "and no interaction residual.")
    return {"preset": preset, "model_fingerprint": fingerprint,
            "digest_at_fork": digest, "control": probe.control,
            "caveats": kept,
            "rows": rows, "seconds": time.time() - started}


def key(row: dict) -> tuple:
    return (row["stream"], row["kind"], row["index"], row["perturbation"])


def difference(a: dict, b: dict) -> dict:
    ka = {key(r): r for r in a["rows"]}
    kb = {key(r): r for r in b["rows"]}
    if set(ka) != set(kb):
        only_a = sorted(set(ka) - set(kb))[:5]
        only_b = sorted(set(kb) - set(ka))[:5]
        raise SystemExit(
            f"the two presets did not attribute the same addresses: "
            f"{len(set(ka) - set(kb))} only under {a['preset']} (first "
            f"{only_a}), {len(set(kb) - set(ka))} only under {b['preset']} "
            f"(first {only_b}). The draw schedule moved between them, which "
            "a preset cannot do; stop here.")
    rows = []
    for k in sorted(ka):
        ra, rb = ka[k], kb[k]
        rows.append(dict(stream=ra["stream"], site=ra["site"], day=ra["day"],
                         tag=ra["tag"], ticker=ra["ticker"], kind=ra["kind"],
                         index=ra["index"], count=ra["count"],
                         perturbation=ra["perturbation"], delta=ra["delta"],
                         effect_a=ra["effect"], effect_b=rb["effect"],
                         difference=rb["effect"] - ra["effect"]))
    return {"rows": rows, "by_site": by_site(rows)}


def by_site(rows: list[dict]) -> list[dict]:
    import numpy as np

    out = []
    groups: dict[tuple, list] = {}
    for r in rows:
        groups.setdefault((r["stream"], r["site"], r["perturbation"]),
                          []).append(r)
    for (stream, site, perturbation), items in sorted(groups.items()):
        ea = np.array([r["effect_a"] for r in items])
        eb = np.array([r["effect_b"] for r in items])
        sum_a, sum_b = float(np.abs(ea).sum()), float(np.abs(eb).sum())
        ratio = (sum_b / sum_a) if sum_a > 0 else None
        corr = None
        if len(items) > 2 and ea.std() > 0 and eb.std() > 0:
            corr = float(np.corrcoef(ea, eb)[0, 1])
        out.append(dict(stream=stream, site=site, perturbation=perturbation,
                        rows=len(items), sum_abs_a=sum_a, sum_abs_b=sum_b,
                        amplification=ratio, correlation=corr,
                        nonzero_a=int((ea != 0).sum()),
                        nonzero_b=int((eb != 0).sum())))
    return out


#: The three cut-offs the verdict column turns into words. They are
#: conventions chosen for this report, not thresholds any measurement set,
#: and they are printed in the report beside the column they decide so a
#: reader is never left to infer them. What decides whether a verdict is
#: offered at all is the standard error beside the correlation, which is a
#: measurement.
CORRELATION_FLOOR = 0.5
AMPLIFIED_ABOVE = 1.25
DAMPED_BELOW = 0.8


def verdict(ratio, corr, rows=None, nonzero=None) -> str:
    """A word for a (ratio, correlation) pair, or a reason for withholding.

    A correlation over few rows is not separable from zero: its standard
    error is about ``1 / sqrt(rows)``, so a site with 200 rows and a
    correlation of 0.06 sits inside one standard error of nothing and
    cannot support "different mechanism", which is what it used to be
    given. The verdict is withheld there and the reader is told why.

    ``nonzero`` is how many of the site's rows contribute anything under
    A. A site whose 16 rows rest on 8 contributing pairs is not 16 rows of
    evidence, and a handful cannot carry a categorical claim at all.
    """
    if ratio is None:
        return "no effect under A"
    if nonzero is not None and nonzero < 3:
        return f"too few contributing rows ({nonzero})"
    if corr is None:
        return "correlation undefined"
    if rows:
        se = 1.0 / math.sqrt(rows)
        if abs(corr) < 2 * se:
            return f"correlation within 2 se ({2 * se:.2f})"
    if corr < CORRELATION_FLOOR:
        return "different mechanism"
    if ratio > AMPLIFIED_ABOVE:
        return "amplified"
    if ratio < DAMPED_BELOW:
        return "damped"
    return "same gain"


def who(row: dict) -> str:
    """What a row is about, for a table column.

    A site that names an instrument prints its ticker. A site that names
    none still carries a tag, and printing neither left two sector rows on
    one day separable only by their numbers.
    """
    if row.get("ticker"):
        return str(row["ticker"])
    if row["site"] == "sector_z":
        return f"sector {row['tag']}"
    if row["site"] in ("market_factor_z", "jump_market_u", "jump_market_z",
                       "volume_z"):
        return "market"
    return f"tag {row['tag']}"


def render(a: dict, b: dict, diff: dict, args: argparse.Namespace,
           provenance: dict) -> str:
    first, last = args.window
    lines = [f"# Amplification: {a['preset']} against {b['preset']}", "",
             "## Setup", "",
             f"- roster: Universe.random({args.names}, seed={args.roster_seed})",
             f"- seed {args.seed}, {args.days} days, window days "
             f"{first}..{last}, arms run to day {args.days - 1}",
             f"- target: {index_return(first, last).__name__}",
             f"- streams {', '.join(args.streams)}, granularity "
             f"{args.granularity}, delta {args.delta}",
             f"- order flow: zero (a flat agent)",
             f"- {a['preset']}: fingerprint {a['model_fingerprint']}, digest "
             f"at the fork {a['digest_at_fork'][:16]}, control "
             f"{a['control']:.6g}",
             f"- {b['preset']}: fingerprint {b['model_fingerprint']}, digest "
             f"at the fork {b['digest_at_fork'][:16]}, control "
             f"{b['control']:.6g}",
             f"- rows {len(diff['rows'])} per preset, "
             f"{a['seconds'] + b['seconds']:.0f} s of arms on "
             f"{args.workers} workers",
             f"- tradefloor {provenance['version']}, commit "
             f"{provenance['commit']}, {provenance['when']}",
             "", "## Gain per site", "",
             "The sum of absolute effects under B over the sum under A, and "
             "the correlation of the effects across presets at the same "
             "address. A ratio away from one with a high correlation is the "
             "same mechanism at another gain; a low correlation is a "
             "different mechanism reading the draw.", "",
             f"The verdict reads a correlation below {CORRELATION_FLOOR} as "
             f"a different mechanism, a ratio above {AMPLIFIED_ABOVE} as "
             f"amplified and one below {DAMPED_BELOW} as damped. Those "
             "three numbers are conventions chosen for this report and no "
             "measurement set them. The standard error column is 1 over "
             "the square root of the row count, and a correlation inside "
             "two of them cannot support a verdict, so none is given. The "
             "nonzero columns are how many rows contribute anything under "
             "each preset, which is what the correlation is actually "
             "taken over.", "",
             "| stream | site | perturbation | rows | nonzero A | nonzero B "
             "| sum abs A | sum abs B | B/A | corr | se | verdict |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in diff["by_site"]:
        ratio = "n/a" if s["amplification"] is None else f"{s['amplification']:.2f}"
        corr = "n/a" if s["correlation"] is None else f"{s['correlation']:.2f}"
        se = f"{1.0 / math.sqrt(s['rows']):.2f}" if s["rows"] else "n/a"
        said = verdict(s["amplification"], s["correlation"], s["rows"],
                       s["nonzero_a"])
        lines.append(f"| {s['stream']} | {s['site']} | {s['perturbation']} | "
                     f"{s['rows']} | {s['nonzero_a']} | {s['nonzero_b']} | "
                     f"{s['sum_abs_a']:.3g} | "
                     f"{s['sum_abs_b']:.3g} | {ratio} | {corr} | {se} | "
                     f"{said} |")
    lines += ["", "## Largest differences", "",
              "| stream | site | day | what | perturbation | effect A | "
              "effect B | B minus A |", "|---|---|---|---|---|---|---|---|"]
    top = sorted(diff["rows"], key=lambda r: -abs(r["difference"]))[:20]
    for r in top:
        lines.append(f"| {r['stream']} | {r['site']} | {r['day']} | "
                     f"{who(r)} | {r['perturbation']} | "
                     f"{r['effect_a']:.3g} | {r['effect_b']:.3g} | "
                     f"{r['difference']:+.3g} |")
    lines += ["", "## Caveats", ""]
    for c in a["caveats"]:
        lines.append(f"- {c}")
    lines += ["- the target is one window's index return at one seed; a "
              "gain that holds across seeds is a claim this report does not "
              "make.",
              "- an effect measured across a circuit breaker or a book "
              "clamp is the clamped effect."]
    return "\n".join(lines) + "\n"


def provenance() -> dict:
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                                capture_output=True, text=True,
                                check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "unknown"
    return {"version": tf.version(), "commit": commit,
            "when": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--presets", nargs=2, default=["pt-v14", "pt-v16"],
                   metavar=("A", "B"))
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--roster-seed", type=int, default=111)
    p.add_argument("--names", type=int, default=40)
    p.add_argument("--days", type=int, default=40,
                   help="days in the run; the arms run to the last")
    p.add_argument("--window", type=int, nargs=2, default=[30, 34],
                   metavar=("FIRST", "LAST"))
    p.add_argument("--streams", default="market,jumps")
    p.add_argument("--granularity", default="both",
                   choices=["event", "day", "both"])
    p.add_argument("--delta", type=float, default=1.0)
    p.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    p.add_argument("--out", required=True)
    a = p.parse_args(argv)
    a.streams = [s.strip() for s in a.streams.split(",") if s.strip()]
    first, last = a.window
    if not 1 <= first <= last < a.days:
        raise SystemExit("the window needs 1 <= first <= last < days: the "
                         "target reads the close before the window")
    os.makedirs(a.out, exist_ok=True)
    when = provenance()
    results = [attribute_preset(name, a) for name in a.presets]
    diff = difference(results[0], results[1])
    with open(os.path.join(a.out, "amplification.json"), "w",
              encoding="utf-8") as f:
        json.dump({"args": vars(a), "provenance": when, "presets": results,
                   "difference": diff}, f, indent=1)
    text = render(results[0], results[1], diff, a, when)
    with open(os.path.join(a.out, "amplification.md"), "w",
              encoding="utf-8") as f:
        f.write(text)
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
