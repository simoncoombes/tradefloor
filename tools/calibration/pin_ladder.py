#!/usr/bin/env python3
"""The VIX loop's static map, and the stability condition read off it.

Under ``vix_level_identity`` the VIX's target IS the level the index's own
conditional variance implies (``market::index_var``), so the loop has a map
and the map has a gain. Nothing measured that gain while the read-back was
blind to the crash amplifier and the crisis blend, and the loop-gain run
listed it as undetermined by its design. It is determinable from here.

THREE THINGS, and they are three because the third is the one that matters
and the first two are what make it a measurement rather than an opinion.

``ladder``
    Pin the VIX with ``Engine.pin_macro`` before every open and read
    ``Engine.index_variance_terms()["implied"]`` after every close.
    ``implied / pinned`` is the static map. Below one the loop contracts
    toward a fixed point; at or above one the level it was pinned at
    sustains itself, and if that holds up to ``vix_ceiling`` the ceiling is
    an absorbing state. ``after - pinned`` is the SAME map read through the
    engine's own VIX update rather than through the identity, and the two
    agreeing is the check that the fear excursion really is zero-mean --
    they agree within a fifth of a VIX point at the fixed point.

``gain``
    The stability condition, solved. Write it as

        implied(v) < v   for every v in (crisis_vix_threshold, vix_ceiling]

    ``implied(v) / v`` rises on the range where the crisis spike is
    saturated -- the amplifier's second moment rises with the regime ratio
    and every other block is at most quadratic in ``v`` -- and the spike is
    saturated at the ceiling for any threshold under
    ``vix_ceiling - ramp * cap``. So the condition binds at the ceiling and
    there alone, and THE BINDING CONSTRAINT DOES NOT CONTAIN
    ``crisis_vix_threshold``. At ``crisis_blend_source`` 1.0 it is a
    quadratic in ``crisis_blend_gain``:

        R [ (b L)^2 tau + b L (2 + d) cap g + cap^2 g^2 ] + Q = v^2 / conv^2 / 252

    with ``R = k v_f E[z^2 A^2]`` the amplified factor variance, ``Q``
    everything the blend does not touch, ``b L`` the transmitted loading,
    ``d = market_beta_down_asym``, ``tau = (1 + (1 + d)^2) / 2`` and
    ``conv = (1 + vix_variance_premium) * 100``. Each of ``R``, ``b L`` and
    ``Q`` is read off a ladder measured with the blend OFF, which is exact:
    under a pin the factor draw, its variance state, the amplifier moment
    and the lag bit are all independent of the blend, because the blend
    re-weights each name's LOADING on the factor and touches nothing else.
    The one approximation is ``Q``, through the per-name GARCH states, which
    is a tenth of ``V_t`` and second order in the gain.

``census``
    How often the loop actually reaches ``vix_ceiling``, free-running, over
    the thirty rosters ``facts.LEVEL_PROTOCOL`` draws. This is the one to
    run before believing a gain the solver returns: the condition above is
    evaluated on the rosters the ladder was measured on, and the loop's gain
    is a property of the roster as well as of the dials.

Usage::

    python tools/calibration/pin_ladder.py ladder \\
        --pins 14,20,26,32,40,50,60,70,80 --seeds 101,102,103 --out ladder.json
    python tools/calibration/pin_ladder.py ladder --blend-off --out calm.json
    python tools/calibration/pin_ladder.py gain --ladder calm.json
    python tools/calibration/pin_ladder.py census --overrides '{"crisis_blend_gain": 0.0}'

Every subcommand takes ``--overrides`` as a JSON object of
``ModelParams`` fields, so a candidate is measured without a rebuild.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

# APPENDED and not inserted, deliberately. Every other tool here expects
# `tradefloor` from the venv `maturin develop` built into, and an installed
# build must keep winning: a source tree that has moved past its extension
# reports drift everywhere and none of it is real (RELEASING.md). This only
# makes the tool runnable from a bare checkout, where the extension in
# `python/tradefloor/` is the build.
sys.path.append(str(Path(__file__).resolve().parents[2] / "python"))

import tradefloor as tf  # noqa: E402
from tradefloor.facts import LEVEL_PROTOCOL  # noqa: E402

#: The preset the loop measurement is about. Written here rather than taken
#: from the default, so the arm a run measures is visible at the call site.
PRESET = "pt-v19"

#: Roster size and rule. `LEVEL_PROTOCOL` draws the roster PER SEED -- thirty
#: rosters certify the model where thirty market seeds on one roster certify
#: that roster -- and the loop's gain is a property of the roster, so the
#: ladder inherits the same rule rather than holding one.
ROSTER = 40

#: Sessions discarded before scoring, and sessions scored. The factor's
#: variance state reverts toward the pinned VIX's target with a persistence
#: of 0.97, so forty sessions is about a dozen half-lives.
BURN, SCORED = 40, 80


def model(overrides: dict) -> "tf.ModelParams":
    d = tf.ModelParams.from_preset(PRESET).to_dict()
    d.update(overrides)
    return tf.ModelParams.from_dict(d)


def _pinned_run(job):
    seed, pin, overrides = job
    universe = tf.Universe.random(ROSTER, seed=seed)
    engine = tf.Engine(seed=seed, universe=universe, model=model(overrides))
    anchor = engine.vix_anchor
    rows = []
    for day in range(BURN + SCORED):
        # BEFORE every open, so the whole session runs at the pinned level
        # and the close reads it. `pin_macro` writes the state; it does not
        # hold it, and the day's update moves it.
        engine.pin_macro(vix=pin)
        engine.open_market()
        engine.run_session(9, 30, 3, 390)
        engine.close_market()
        terms = engine.index_variance_terms()
        if terms is None:
            raise SystemExit(
                f"{PRESET} reported no index variance terms: the ladder needs "
                f"vix_level_identity on, and this arm has it off")
        snap = engine.state_snapshot()
        if day >= BURN:
            rows.append({
                "seed": seed, "pin": pin, "anchor": anchor,
                "factor": terms["factor"], "crash": terms["crash"],
                "crisis": terms["crisis"], "tilt": terms["tilt"],
                "sector": terms["sector"], "idio": terms["idio"],
                "k": terms["k"], "total": terms["total"],
                "implied": terms["implied"],
                "v_f": snap["market_variance"][0],
                "after": snap["economy"]["vix"],
            })
    return rows


def _free_run(job):
    seed, days, overrides = job
    universe = tf.Universe.random(ROSTER, seed=seed)
    engine = tf.Engine(seed=seed, universe=universe, model=model(overrides))
    out = []
    for _ in range(days):
        engine.open_market()
        engine.run_session(9, 30, 3, 390)
        engine.close_market()
        out.append(engine.state_snapshot()["economy"]["vix"])
    return out


# ── ladder ───────────────────────────────────────────────────────────────


def cmd_ladder(args):
    overrides = json.loads(args.overrides)
    if args.blend_off:
        # The blend switched OFF, not reduced: the gain solver needs a ladder
        # in which `crisis_raw` is exactly zero, so that what it reads back
        # is the map the blend acts ON rather than a map it is already in.
        overrides["crisis_blend_gain"] = 0.0
        overrides["crisis_vix_threshold"] = 1.0e6
    pins = [float(x) for x in args.pins.split(",")]
    seeds = [int(x) for x in args.seeds.split(",")]
    jobs = [(s, p, overrides) for p in pins for s in seeds]
    rows = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for chunk in pool.map(_pinned_run, jobs):
            rows.extend(chunk)
    if args.out:
        json.dump({"preset": PRESET, "overrides": overrides, "seeds": seeds,
                   "burn": BURN, "scored": SCORED, "rows": rows},
                  open(args.out, "w"))
    by_pin: dict[float, list] = {}
    for r in rows:
        by_pin.setdefault(r["pin"], []).append(r)
    print(f"{PRESET} + {json.dumps(overrides)}")
    print(f"seeds {seeds}, {SCORED} scored sessions per seed per pin")
    print(f"{'pinned':>8} {'implied':>9} {'imp/pin':>8} {'after-pin':>10} "
          f"{'v_f/base':>9} {'anchor':>8}")
    base = tf.ModelParams.from_preset(PRESET).to_dict()["market_factor_sigma"] ** 2
    for pin in pins:
        rs = by_pin[pin]
        implied = statistics.fmean(r["implied"] for r in rs)
        after = statistics.fmean(r["after"] for r in rs)
        v_f = statistics.median(r["v_f"] for r in rs) / base
        anchor = statistics.fmean(r["anchor"] for r in rs)
        print(f"{pin:8.2f} {implied:9.3f} {implied / pin:8.3f} "
              f"{after - pin:10.3f} {v_f:9.2f} {anchor:8.3f}")
    print("\nimp/pin under 1 contracts; at or above 1 the pinned level "
          "sustains itself.\nafter-pin is the same map through the engine's "
          "own update: the two agreeing\nis the evidence that the fear "
          "excursion is zero-mean, which is what lets\nthe gain below be "
          "solved on the identity alone.")


# ── gain ─────────────────────────────────────────────────────────────────


def _prepare(path):
    blob = json.load(open(path))
    out = []
    for r in blob["rows"]:
        if r["crisis"] != 0.0:
            raise SystemExit(
                "the gain is solved from a ladder measured with the blend "
                "OFF, and this one carries a crisis term; re-run `ladder` "
                "with --blend-off")
        # `beta_w` per day, from the identity's own factor block:
        # `factor_raw = beta_w^2 v_f`, and both sides are in the row. The
        # weights are previous-close capitalisation weights and move with
        # the roster, so it is read per day rather than assumed constant.
        b = math.sqrt(r["factor"] / r["v_f"])
        amplified = r["factor"] + r["crash"]
        out.append({
            "pin": r["pin"],
            # The transmitted loading `b L`, with the lag multiplier
            # recovered from the tilt block the day reported.
            "b": b, "tilt": r["tilt"], "amplified": amplified,
            # `R = k v_f E[z^2 A^2]`, and `Q` everything else.
            "R": r["k"] * amplified / (b * b),
            "Q": r["total"] - r["k"] * (amplified + r["tilt"]),
        })
    return blob, out


def cmd_gain(args):
    blob, rows = _prepare(args.ladder)
    p = tf.ModelParams.from_preset(PRESET).to_dict()
    p.update(json.loads(args.overrides))
    d = p["market_beta_down_asym"]
    cap = p["crisis_blend_cap"]
    ramp = p["crisis_blend_ramp"]
    ceiling = args.at if args.at is not None else p["vix_ceiling"]
    tau = (1.0 + (1.0 + d) ** 2) / 2.0
    conv = (1.0 + p["vix_variance_premium"]) * 100.0
    if p["crisis_blend_source"] != 1.0:
        raise SystemExit(
            "the closed form below drops the sector leak, which is zero only "
            "at crisis_blend_source 1.0; at any other wiring solve the "
            "quadratic with `C` carried")
    at = [r for r in rows if r["pin"] == ceiling]
    if not at:
        raise SystemExit(
            f"the condition binds at {ceiling}, and the ladder has no pin "
            f"there; re-run `ladder` including it")
    n = len(at)
    # `b L` per day. At zero spike the tilt block is `R (P2 - b^2) / k` and
    # `P2 = (b L)^2 tau`, so `(b L)^2 = b^2 (1 + tilt / amplified) / tau` --
    # the `tau` divides out here and multiplies back in `c0`, which is the
    # check that the lag multiplier and the tick-sign tilt are two factors
    # and not one.
    bl = [r["b"] * math.sqrt((1.0 + r["tilt"] / r["amplified"]) / tau)
          for r in at]
    c2 = statistics.fmean(r["R"] for r in at) * cap * cap
    c1 = statistics.fmean(r["R"] * l for r, l in zip(at, bl)) * (2.0 + d) * cap
    c0 = statistics.fmean(r["R"] * l * l * tau + r["Q"]
                          for r, l in zip(at, bl))
    rhs = ceiling * ceiling / (conv * conv * 252.0)
    disc = c1 * c1 - 4.0 * c2 * (c0 - rhs)
    print(f"{PRESET} + {json.dumps(blob['overrides'])}")
    print(f"the stability condition implied(v) < v binds at v = {ceiling}")
    print(f"  quadratic  {c2:.6e} g^2 + {c1:.6e} g + {c0 - rhs:.6e} = 0")
    if disc < 0.0 or c0 > rhs:
        print(f"  NO POSITIVE ROOT: the condition already fails at gain 0 "
              f"({conv * math.sqrt(252.0 * c0):.2f} implied against {ceiling}). "
              f"The crisis blend is not what is holding this map up.")
        return
    root = (-c1 + math.sqrt(disc)) / (2.0 * c2)
    print(f"  crisis_blend_gain <= {root:.8f}   "
          f"(shipped {p['crisis_blend_gain']:.7f})")
    print(f"  the spike saturates {ramp * cap:.3f} points above the "
          f"threshold, so any threshold under {ceiling - ramp * cap:.3f} "
          f"gives the same root")
    print("\nSolved on the MEAN variance, which is the conservative side: "
          "sqrt is concave,\nso the mean implied VIX the loop actually drifts "
          "on sits below the implied\nVIX of the mean variance, and the root "
          "leaves a margin rather than sitting on\nthe boundary. Re-measure "
          "with `ladder` at the root before believing it, and\nwith `census` "
          "over the full roster draw before shipping it.")


# ── census ───────────────────────────────────────────────────────────────


def cmd_census(args):
    overrides = json.loads(args.overrides)
    seeds = list(LEVEL_PROTOCOL["seeds"])
    jobs = [(s, args.days, overrides) for s in seeds]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        runs = dict(zip(seeds, pool.map(_free_run, jobs)))
    p = tf.ModelParams.from_preset(PRESET).to_dict()
    p.update(overrides)
    ceiling, threshold = p["vix_ceiling"], p["crisis_vix_threshold"]
    every = [v for s in seeds for v in runs[s]]
    n = len(every)
    reached = sum(1 for v in every if v >= ceiling - 1e-9)
    print(f"{PRESET} + {json.dumps(overrides)}")
    print(f"{len(seeds)} rosters x {args.days} sessions = {n} seed-days, "
          f"the roster drawn per seed as facts.LEVEL_PROTOCOL draws it")
    print(f"  mean {statistics.fmean(every):.2f}  "
          f"median {statistics.median(every):.2f}  max {max(every):.2f}")
    print(f"  above crisis_vix_threshold {threshold:.4f}: "
          f"{100.0 * sum(1 for v in every if v > threshold) / n:.2f}%")
    print(f"  at vix_ceiling {ceiling}: {reached} of {n}")
    hot = [(s, statistics.fmean(runs[s]),
            sum(1 for v in runs[s] if v >= ceiling - 1e-9))
           for s in seeds if max(runs[s]) >= ceiling - 1e-9]
    if hot:
        print("  rosters reaching the ceiling (seed, mean VIX, sessions):")
        for s, m, c in hot:
            print(f"    {s}  {m:6.2f}  {c}")
    else:
        print("  no roster reached the ceiling")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--overrides", default="{}",
                    help="JSON object of ModelParams fields")
    ap.add_argument("--workers", type=int, default=8)
    sub = ap.add_subparsers(dest="cmd", required=True)

    lad = sub.add_parser("ladder")
    lad.add_argument("--pins", default="14,20,26,32,40,50,60,70,80",
                     help="absolute VIX levels")
    lad.add_argument("--seeds", default="101,102,103")
    lad.add_argument("--blend-off", action="store_true",
                     help="switch the crisis blend off, which is what the "
                          "gain solver needs")
    lad.add_argument("--out")
    lad.set_defaults(func=cmd_ladder)

    gai = sub.add_parser("gain")
    gai.add_argument("--ladder", required=True, help="a --blend-off ladder")
    gai.add_argument("--at", type=float, default=None,
                     help="evaluate the condition at this VIX instead of "
                          "vix_ceiling")
    gai.set_defaults(func=cmd_gain)

    cen = sub.add_parser("census")
    cen.add_argument("--days", type=int, default=252)
    cen.set_defaults(func=cmd_census)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
