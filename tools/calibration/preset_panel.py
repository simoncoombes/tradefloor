"""Measure EVERY shipped preset on ONE ruler, so they can be ranked.

Why this exists. The site's preset table is twelve rows of prose, and each
row quotes the panel count that was current when that preset shipped: pt-v5
says "9 of 10", pt-v7 says "twelve of thirteen", pt-v10 says "all fourteen".
Those are three different rulers. The panel grew from ten statistics to
thirteen to fourteen, so a reader comparing the rows is comparing counts that
do not mean the same thing, and a table that ranks them would rank the
rulers rather than the presets.

This measures all twelve on the fourteen-statistic panel, at thirty seeds,
on the certified roster, at both horizons, on a universe none of them was
tuned on, and under a held crisis. One method, one ruler, one run.

Every number the ranking table publishes comes from here. Nothing is carried
forward from a calibration record, because that is how the current
table drifted.

The method, spelled out because "check which one you are reading" is a
standing warning on the realism page:

  panel_252     Universe.random(40, seed=111), 252 days, seeds 101-130,
                scored against facts.REAL_MARKETS.
  panel_504     the same roster and seeds at 504 days, scored against
                facts.REAL_MARKETS_504 -- the horizon-matched ruler. Scoring
                a 504-day run against the 252-day bands is trap 2 of the
                runbook and the scales differ by 0.8x to 3.2x.
  heldout_universe
                Universe.random(60, seed=909), 252 days, seeds 101-130. A
                roster no preset was calibrated on.
  heldout_seeds Universe.random(40, seed=111), 252 days, seeds 1-30. THIRTY,
                against the six gate_pick screens on: trap 15, where a six-seed
                read called pt-v10 13/14 and thirty called it 14/14 because
                corr_persistence_acf1 has an across-seed sd of 0.28.
  crisis_lever  annualised volatility under a held VIX 65 divided by the same
                under a held VIX 5, on the CERTIFIED roster over 252 days at
                thirty seeds. This is deliberately NOT scenario_response's
                held-VIX half, which pins 120 days on a 20-name roster and
                answers a different question with a similar-looking number.

Usage:
    python tools/calibration/preset_panel.py --workers 190 \\
        --out /home/ec2-user/out/preset-panel.json
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import pathlib
import statistics
import sys
import time

import tradefloor
import tradefloor.facts as facts
from tradefloor import Scenario, envelope

#: The published method. Both are what `tradefloor.envelope` certifies against.
ROSTER_N, ROSTER_SEED = 40, 111
HELDOUT_N, HELDOUT_SEED = 60, 909

#: Thirty, everywhere. See the module docstring on trap 15.
TRAIN_SEEDS = tuple(range(101, 131))
HELDOUT_SEEDS = tuple(range(1, 31))

#: The fourteen. Taken from `envelope.CERTIFIED` rather than written out, so
#: a fifteenth statistic joins this table by being added to the envelope.
PANEL = tuple(envelope.CERTIFIED)

#: The crisis lever's two endpoints, and the real-market figure it is read
#: against (17.2% annualised below VIX 12 against 106.1% above VIX 45, from
#: `real_vix_lever.py`; the ratio is 6.16).
LEVER_LO, LEVER_HI = 5.0, 65.0
REAL_LEVER = 6.16


#: How the engine quotes its own preset list when it refuses a name, from
#: `ModelParams::preset_names()` in `rust/src/params.rs`. That list is the
#: one authoritative enumeration and it is not on the Python surface, so it
#: is read back out of the refusal. See `presets`.
_SHIPPED_PREFIX = "Shipped presets: "


def presets() -> list[str]:
    """Every selectable preset, from the engine's own list.

    THE LIST IS NOT A RANGE. This walked `pt-v1..pt-v199` and broke at the
    first name that did not resolve once it had found any, which is correct
    only while the names are contiguous. They stopped being contiguous when
    pt-v17 was abandoned and pt-v18 shipped: the walk stopped at 17 and
    pt-v18 -- and every preset after it -- was never measured, so
    `record.py` could not write a record for the preset that was about to
    become the default and nothing said why. A gap is a fact about the
    naming, not the end of the table.

    `ModelParams::preset_names()` is the enumeration the Rust side already
    keeps, and every Python entry point that refuses an unknown preset
    quotes it in the refusal. It is not otherwise exposed, so this reads it
    back from that message. Ugly, and single-sourced: the alternative is a
    second list that can disagree with the first, which is the class of
    defect this function just had.

    The scan is kept as a CROSS-CHECK rather than as the answer. Every name
    it finds must appear in the engine's list; a disagreement is refused
    loudly, because under-enumerating silently is what cost the gate.
    """
    shipped: list[str] = []
    try:
        tradefloor.model_preset("pt-v0")
    except Exception as exc:  # the refusal quotes preset_names()
        message = str(exc)
        if _SHIPPED_PREFIX in message:
            shipped = [n.strip() for n
                       in message.split(_SHIPPED_PREFIX, 1)[1].split(",")
                       if n.strip()]

    # Names that actually resolve, so a parse that picked up trailing prose
    # cannot put an unrunnable name into a measurement.
    resolvable = []
    for name in shipped:
        try:
            tradefloor.model_preset(name)
        except Exception:
            continue
        resolvable.append(name)

    scanned = []
    for i in range(1, 200):
        name = f"pt-v{i}"
        try:
            tradefloor.model_preset(name)
        except Exception:
            continue
        scanned.append(name)

    if not resolvable:
        # The refusal's wording changed. The scan still answers for every
        # `pt-vN` name, which is all of them today, and says so rather than
        # returning a shorter list in silence.
        print("WARNING: could not read the engine's preset list from its "
              "refusal message; falling back to the pt-vN scan", flush=True)
        return scanned
    missing = [n for n in scanned if n not in resolvable]
    if missing:
        raise SystemExit(
            f"the engine's preset list omits {missing}, which resolve: the "
            "refusal message and the preset table disagree, and measuring "
            "either list would be measuring the wrong set"
        )
    return resolvable


def _commit() -> str | None:
    """This checkout's HEAD, or None outside a repository."""
    import subprocess  # noqa: PLC0415

    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                             text=True, cwd=str(pathlib.Path(__file__).resolve()
                                                .parents[2]))
    except OSError:
        return None
    return out.stdout.strip() or None


def _roster(n: int, seed: int):
    return tradefloor.Universe.random(n, seed=seed)


def _job(spec):
    """One measurement in a worker. Returns (key, preset, seed, panel)."""
    key, preset, seed = spec
    if key == "panel_252":
        p = facts.measure(seed=seed, universe=_roster(ROSTER_N, ROSTER_SEED),
                          days=252, model=preset)
    elif key == "panel_504":
        p = facts.measure(seed=seed, universe=_roster(ROSTER_N, ROSTER_SEED),
                          days=504, model=preset)
    elif key == "heldout_universe":
        p = facts.measure(seed=seed, universe=_roster(HELDOUT_N, HELDOUT_SEED),
                          days=252, model=preset)
    elif key == "heldout_seeds":
        p = facts.measure(seed=seed, universe=_roster(ROSTER_N, ROSTER_SEED),
                          days=252, model=preset)
    elif key in ("lever_lo", "lever_hi"):
        vix = LEVER_LO if key == "lever_lo" else LEVER_HI
        p = facts.measure(seed=seed, universe=_roster(ROSTER_N, ROSTER_SEED),
                          days=252, model=preset,
                          scenario=Scenario().hold(vix=vix))
    else:
        raise ValueError(key)
    return key, preset, seed, {k: p[k] for k in PANEL}


def _median_panel(rows: list[dict]) -> dict:
    # Each row by its own estimator: medians for the shape rows and a mean
    # for the level row, which is what the band's width was set against.
    return {k: facts.aggregate_value(k, [r[k] for r in rows if r.get(k) is not None])
            for k in PANEL if any(r.get(k) is not None for r in rows)}


def _count_in_band(panel: dict, bands: dict) -> tuple[int, list[str]]:
    """How many of the fourteen sit inside their band, and which do not."""
    misses = []
    for k in PANEL:
        lo, hi = bands[k]
        if not (lo <= panel[k] <= hi):
            misses.append(k)
    return len(PANEL) - len(misses), misses


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", help="comma-separated presets, for a smoke test")
    ap.add_argument("--seeds", type=int,
                    help="cap the seed count. FOR SMOKE TESTS ONLY: every "
                         "published count here is a thirty-seed median, and "
                         "a count without its seed count is trap 15.")
    args = ap.parse_args()

    names = args.only.split(",") if args.only else presets()
    train = TRAIN_SEEDS[:args.seeds] if args.seeds else TRAIN_SEEDS
    heldout = HELDOUT_SEEDS[:args.seeds] if args.seeds else HELDOUT_SEEDS
    started = time.time()

    specs = []
    for preset in names:
        for seed in train:
            specs += [("panel_252", preset, seed),
                      ("panel_504", preset, seed),
                      ("heldout_universe", preset, seed),
                      ("lever_lo", preset, seed),
                      ("lever_hi", preset, seed)]
        for seed in heldout:
            specs.append(("heldout_seeds", preset, seed))

    print(f"{len(names)} presets, {len(specs)} measurements, "
          f"{args.workers} workers", flush=True)

    # A pool, and therefore a __main__ guard at the bottom of this file:
    # macOS spawns rather than forks, so a module-level pool re-imports the
    # module in every worker and forks bombs. Trap 5 of the runbook.
    collected: dict[tuple[str, str], list[dict]] = {}
    done = 0
    with mp.Pool(args.workers) as pool:
        for key, preset, seed, panel in pool.imap_unordered(_job, specs,
                                                            chunksize=1):
            collected.setdefault((key, preset), []).append(panel)
            done += 1
            if done % 100 == 0:
                rate = done / max(time.time() - started, 1e-9)
                print(f"  {done}/{len(specs)}  {rate:.1f}/s  "
                      f"eta {(len(specs) - done) / max(rate, 1e-9) / 60:.1f}m",
                      flush=True)

    results = {}
    for preset in names:
        p252 = _median_panel(collected[("panel_252", preset)])
        p504 = _median_panel(collected[("panel_504", preset)])
        phou = _median_panel(collected[("heldout_universe", preset)])
        phos = _median_panel(collected[("heldout_seeds", preset)])
        lo = _median_panel(collected[("lever_lo", preset)])
        hi = _median_panel(collected[("lever_hi", preset)])

        n252, miss252 = _count_in_band(p252, facts.REAL_MARKETS)
        n504, miss504 = _count_in_band(p504, facts.REAL_MARKETS_504)
        nhou, misshou = _count_in_band(phou, facts.REAL_MARKETS)
        nhos, misshos = _count_in_band(phos, facts.REAL_MARKETS)

        results[preset] = {
            "panel_252": p252,
            "panel_504": p504,
            "in_band_252": n252, "misses_252": miss252,
            "in_band_504": n504, "misses_504": miss504,
            "in_band_heldout_universe": nhou, "misses_heldout_universe": misshou,
            "in_band_heldout_seeds": nhos, "misses_heldout_seeds": misshos,
            "annualised_vol_pct": p252["annualised_vol_pct"],
            "vol_at_vix_5": lo["annualised_vol_pct"],
            "vol_at_vix_65": hi["annualised_vol_pct"],
            "crisis_lever": hi["annualised_vol_pct"] / lo["annualised_vol_pct"],
        }
        r = results[preset]
        print(f"{preset:8s} 252:{n252:2d}/14  504:{n504:2d}/14  "
              f"hoU:{nhou:2d}/14  hoS:{nhos:2d}/14  "
              f"vol:{r['annualised_vol_pct']:5.1f}%  "
              f"lever:{r['crisis_lever']:.2f}x", flush=True)

    out = {
        "pretium_version": tradefloor.version(),
        # The commit these numbers were measured at. A version string is not
        # one: 0.6.2 spans every commit between two releases, and a preset
        # can change inside that span -- pt-v18 gained a dial mid-version
        # and moved 1.75 points a year on the level row. Without this,
        # `record.py` had nothing to stamp but the HEAD of whatever
        # checkout ran it afterwards, which is a different machine on a
        # different day.
        "commit": _commit(),
        # The ENGINE's default, not the envelope's claim about it. This field
        # read `envelope.PRESET` until 0.6.0, so at an era boundary, which is
        # exactly when this tool runs, the artefact labelled itself with the
        # preset the envelope still described rather than the one measured.
        "default_preset": tradefloor.model_preset()["name"],
        "envelope_preset": envelope.PRESET,
        "wall_s": time.time() - started,
        "workers": args.workers,
        "panel": list(PANEL),
        "method": {
            "roster": f"Universe.random({ROSTER_N}, seed={ROSTER_SEED})",
            "heldout_universe": f"Universe.random({HELDOUT_N}, seed={HELDOUT_SEED})",
            "train_seeds": f"{train[0]}-{train[-1]} ({len(train)})",
            "heldout_seeds": f"{heldout[0]}-{heldout[-1]} ({len(heldout)})",
            "bands_252": "facts.REAL_MARKETS",
            "bands_504": "facts.REAL_MARKETS_504",
            "crisis_lever": (
                f"annualised vol at held VIX {LEVER_HI:.0f} over held VIX "
                f"{LEVER_LO:.0f}, certified roster, 252 days, thirty seeds"
            ),
            "real_crisis_lever": REAL_LEVER,
        },
        "presets": results,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    print(f"wrote {args.out} in {out['wall_s']:.0f}s", flush=True)


if __name__ == "__main__":
    sys.exit(main())
