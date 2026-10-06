"""ptv19r1 -- named arms on the pt-v19 base, each measured on every
certifying protocol the bar uses (adapted from bestof/run.py; the
families become --arm name:dial=value).

THE ARMS. Each --arm is a name and a comma list of dial=value set on
the base preset (VIXLEVEL_BASE, default pt-v19). An arm with no dials
is the base itself, an instrument check against the committed record.


THE PROTOCOLS, each the one the bar names for its rows (preset_panel._job
and facts.LEVEL_PROTOCOL, mirrored, not copied):
  held    facts.measure on Universe.random(40, seed=111), market seed s,
          252 days, seeds 101-130: the fourteen shape rows and
          vix_ar1_debiased, the way the record's panel_252 and structure
          block read them.
  vary    facts.measure on Universe.random(40, seed=s), market seed s,
          252 days, seeds 101-130: index_drift_pct and the three crisis
          rows, the way level_protocol certifies them.
  lever   facts.measure on the held roster, 252 days after a 252-session
          burn, under Scenario().hold(vix=5) and hold(vix=65), TEN seeds
          101-110: a SCREEN of the crisis lever, not the record's thirty.

TRAPS (HARNESS-NOTES). 1: rows keyed by seed. 2: the commit is read from
the engine tree. 8: the loaded .so is stat'd. Every override is checked to
have landed and to have changed only its family's dials.
"""
import argparse, itertools, json, multiprocessing as mp, os, pathlib
import subprocess, sys, time

# On a box the runner exports REPO (the checkout) and the jobs script exports BESTOF_OUT;
# locally the defaults are the desk paths. Same file both places, no copy to drift.
ENGINE = os.environ.get("REPO", "/Users/simoncoombes/Dev/tradefloor")
OUT = pathlib.Path(os.environ.get("BESTOF_OUT", "/Users/simoncoombes/Dev/tradefloor-design/programme/results/ptv19r1/local"))
BASE = os.environ.get("VIXLEVEL_BASE", "pt-v19")
UNIVERSE_N, HELD_ROSTER_SEED, LEVER_BURN, LEVER_LO, LEVER_HI = 40, 111, 252, 5.0, 65.0
# The HELD roster can be swapped for the held-out universe, Universe.random(60,
# seed=909), which is preset_panel's `heldout_universe` cell. The varying
# roster stays at forty per seed either way.
HELD_N = int(os.environ.get("HELD_N", "40"))
HELD_ROSTER_SEED = int(os.environ.get("HELD_SEED", str(HELD_ROSTER_SEED)))
DAYS = 252   # overridden by --days; the lever is always 252 after its burn
FAMILIES = ("A_vix", "B_mvol", "C_sector", "D_idio", "E_crisis", "F_rest")

tf = facts = ModelParams = Scenario = None
CFG = {}


def _bootstrap(cfg):
    global tf, facts, ModelParams, Scenario, CFG
    p = ENGINE + "/python"
    # An editable desk install carries _core beside the source; a box installs the
    # wheel into its venv, and putting the bare source tree first would shadow it.
    if p not in sys.path and any(pathlib.Path(p, "tradefloor").glob("_core*.so")):   # not the .pyi stub
        sys.path.insert(0, p)
    import tradefloor as _tf
    from tradefloor import facts as _f, ModelParams as _M, Scenario as _S
    tf, facts, ModelParams, Scenario = _tf, _f, _M, _S
    CFG = cfg


def families(v18: dict, v19: dict) -> dict[str, list[str]]:
    diff = sorted(k for k in v18 if v18[k] != v19.get(k) and k != "name")
    # market_vol_vix_excursion is REFUSED by the engine unless vix_level_identity
    # is on (params.rs, "there is no override for this one"): it reads the VIX's
    # excursion above the identity's own level, so it is part of the VIX law and
    # not of the variance process. Moved into A_vix on 2026-09-20 after cell
    # 010000 was refused; no cell completed before the move is affected, since
    # the first seventeen all had A and B both off or both on.
    G = {"A_vix": [k for k in diff if k.startswith("vix_") or k == "market_vol_vix_excursion"],
         "B_mvol": [k for k in diff if k.startswith("market_vol_") and k != "market_vol_vix_excursion"],
         "C_sector": [k for k in diff if k.startswith("sector_")],
         "D_idio": [k for k in diff if k.startswith("jump_idio_")],
         "E_crisis": [k for k in diff if k in ("crash_amplifier_conditional_sigma",
                                               "crisis_blend_gain")]}
    G["F_rest"] = [k for k in diff if not any(k in v for v in G.values())]
    assert sum(len(v) for v in G.values()) == len(diff) == 31, (len(diff), G)
    return G


def _job(spec):
    key, seed, overrides, days = spec[:4]
    base = spec[4] if len(spec) > 4 else BASE
    model = ModelParams.from_preset(base, **overrides) if overrides else ModelParams.from_preset(base)
    held = list(tf.Universe.random(HELD_N, seed=HELD_ROSTER_SEED))
    if key == "held":
        p = facts.measure(seed=seed, universe=held, days=days, model=model)
    elif key == "vary":
        p = facts.measure(seed=seed, universe=list(tf.Universe.random(UNIVERSE_N, seed=seed)),
                          days=days, model=model)
    elif key in ("lever_lo", "lever_hi"):
        vix = LEVER_LO if key == "lever_lo" else LEVER_HI
        p = facts.measure(seed=seed, universe=held, days=252, burn=LEVER_BURN, model=model,
                          scenario=Scenario().hold(vix=vix))
    else:
        raise ValueError(key)
    return key, seed, dict(p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--seeds", default="101-130")
    ap.add_argument("--lever-seeds", default="101-110")
    ap.add_argument("--arm", action="append", default=[],
                    help="name[@BASE]:dial=value,dial=value ; an empty body is the base; @BASE puts the arm "
                         "on that preset instead of VIXLEVEL_BASE (a preset measured by name)")
    ap.add_argument("--days", type=int, default=252)
    ap.add_argument("--no-lever", action="store_true", help="skip the lever runs (reuse the 252 screen)")
    a = ap.parse_args()
    days = a.days
    CELLS = OUT / ("cells" if days == 252 else f"cells{days}")
    lo, hi = a.seeds.split("-"); seeds = list(range(int(lo), int(hi) + 1))
    llo, lhi = a.lever_seeds.split("-"); lseeds = list(range(int(llo), int(lhi) + 1))
    _bootstrap({})
    OUT.mkdir(parents=True, exist_ok=True)

    commit = subprocess.run(["git", "-C", ENGINE, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    so = next(iter(pathlib.Path(tf.__file__).parent.glob("_core*")), None)
    build = {"so": str(so), "so_mtime": time.strftime("%F %T", time.localtime(os.path.getmtime(so))) if so else None,
             "rust_commit": subprocess.run(["git", "-C", ENGINE, "log", "-1", "--date=format:%F %T", "--format=%cd %h", "--", "rust/"], capture_output=True, text=True).stdout.strip(),
             "rust_dirty": subprocess.run(["git", "-C", ENGINE, "status", "--porcelain", "--", "rust/"], capture_output=True, text=True).stdout.strip(),
             "branch": subprocess.run(["git", "-C", ENGINE, "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True).stdout.strip(),
             "python": sys.version.split()[0], "engine_file": tf.__file__}
    G = {}
    json.dump({"commit": commit, "build": build, "base": BASE, "seeds": seeds, "lever_seeds": lseeds,
               "protocols": {"held": "Universe.random(40, seed=111), market seed s, 252 days",
                             "vary": "Universe.random(40, seed=s), market seed s, 252 days (facts.LEVEL_PROTOCOL)",
                             "lever": "held roster, 252 days after 252 burn, Scenario().hold(vix=5|65), the --lever-seeds"}},
              open(OUT / "design.json", "w"), indent=1)
    print("BUILD", json.dumps(build), flush=True)
    print("BASE", BASE, flush=True)

    arms, bases = {}, {}
    for text in a.arm:
        head, body = text.split(":", 1)
        name, _, base = head.strip().partition("@")
        name = name.strip()
        bases[name] = base.strip() or BASE
        arms[name] = {k.strip(): float(v) for k, v in (piece.split("=", 1) for piece in body.split(",") if piece.strip())}
    t_all = time.time()
    with mp.get_context("spawn").Pool(a.workers, initializer=_bootstrap, initargs=({},)) as pool:
        for cell, ov in arms.items():
            path = CELLS / f"cell-{cell}.json"
            if path.exists():
                print(f"{cell} exists, skipped", flush=True); continue
            base = bases[cell]
            base_dict = ModelParams.from_preset(base).to_dict()
            try:
                d = ModelParams.from_preset(base, **ov).to_dict() if ov else base_dict
            except Exception as ex:
                print(f"REFUSED-BY-ENGINE {cell}: {type(ex).__name__}: {str(ex)[:300]}", flush=True); continue
            landed = all(d.get(k) == v for k, v in ov.items())
            changed = sorted(k for k in d if d.get(k) != base_dict.get(k) and k != "name")
            if not landed or set(changed) != set(ov):
                print(f"REFUSED {cell}: landed={landed} changed={changed}", flush=True); continue
            t0 = time.time()
            specs = [("held", s, ov, days, base) for s in seeds] + [("vary", s, ov, days, base) for s in seeds]
            if os.environ.get("NO_VARY"):
                specs = [x for x in specs if x[0] != "vary"]
            if not a.no_lever:
                specs += [("lever_lo", s, ov, days, base) for s in lseeds] + [("lever_hi", s, ov, days, base) for s in lseeds]
            got = {}
            try:
                for key, seed, p in pool.imap_unordered(_job, specs):
                    got[(key, seed)] = p
            except Exception as ex:   # an engine refusal of the cell's vector is a result, not a crash
                print(f"REFUSED-BY-ENGINE {cell}: {type(ex).__name__}: {str(ex)[:300]}", flush=True)
                CELLS.mkdir(parents=True, exist_ok=True)
                json.dump({"kind": "bestof.cell", "cell": cell, "refused": f"{type(ex).__name__}: {ex}", "overrides": ov},
                          open(CELLS / f"cell-{cell}.refused.json", "w"), indent=1)
                continue
            art = {"kind": "bestof.cell", "cell": cell, "base": base,
                   "overrides": ov, "changed_dials": changed, "commit": commit, "build": build,
                   "fingerprint": ModelParams.from_preset(base, **ov).fingerprint,
                   "seeds": seeds, "lever_seeds": lseeds if not a.no_lever else [], "days": days,
                   "held": [got[("held", s)] for s in seeds],
                   "vary": [got[("vary", s)] for s in seeds] if not os.environ.get("NO_VARY") else [],
                   "held_roster": [HELD_N, HELD_ROSTER_SEED],
                   "lever_lo": [got[("lever_lo", s)] for s in lseeds] if not a.no_lever else [],
                   "lever_hi": [got[("lever_hi", s)] for s in lseeds] if not a.no_lever else [],
                   "elapsed_s": time.time() - t0}
            path.parent.mkdir(parents=True, exist_ok=True)
            json.dump(art, open(path, "w"))
            import statistics as st
            print("%s  vol %.2f sector %.4f dn3 %.3f ar1 %.4f lever %.2fx  (%.0fs, %.0fs total)" % (
                cell, st.median(p["annualised_vol_pct"] for p in art["held"]),
                st.median(p["sector_excess_corr"] for p in art["held"]),
                st.median(x for p in art["vary"] for x in p.get("fear_gauge_dn3_samples") or []) if any(p.get("fear_gauge_dn3_samples") for p in art["vary"] or []) else float("nan"),
                st.median(p["vix_ar1_debiased"] for p in art["held"]),
                (st.median(p["annualised_vol_pct"] for p in art["lever_hi"]) / st.median(p["annualised_vol_pct"] for p in art["lever_lo"])) if art["lever_lo"] else float("nan"),
                art["elapsed_s"], time.time() - t_all), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
