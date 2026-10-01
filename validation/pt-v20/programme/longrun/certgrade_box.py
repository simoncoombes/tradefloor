"""Grade the certification cells a box wrote (box-jobs.sh, certrun_box.py).

    python certgrade_box.py BOXDIR --engine ENGINE_WORKTREE [--out FILE] [--json FILE]

BOXDIR is a collected box directory holding cert/, heldseeds/ and heldu/ (any
may be missing), each with cells/ (252 sessions) and cells504/ (504). Every
arm found is graded with the certification's own functions, the same ones
programme/results/route1-blend/certgrade.py used on the desk:

  bands     preset_panel._median_panel and _count_in_band against the default
            basis, on panel_252 (cert 252), panel_504 (cert 504), the held-out
            seeds (heldseeds 252) and the held-out universe (heldu 252)
  mechanism envelope.certify on both 252 panels
  VIX row   envelope.certify_structure at 252, 504 and on the held-out seeds,
            envelope.certify_structure_rise across 252 and 504
  lever     median annualised vol under held VIX 65 over held VIX 5
  level     facts.aggregate_panels on the varying-roster panels (cert 252)

The grader reads the engine's python package, so point --engine at a worktree
whose build knows every dial the arms set.

D1's crisis sector dispersion needs at least ten readings (ptv20-registration.md,
owner 2026-09-26). In each of the four cells the row `crisis_sector_dispersion`
is graded on the median over every reading: the cell's own seeds and, where
those gave fewer than 10, the extension crisisext_box.py ran on the box
(<sub>/crisisext[504]/ext-<cell>.json, blocks of 30 seeds from 1001, stopping
at the first block that brings the count to 10, the last 1241-1270). Fewer
than 10 after 300 seeds in all (the cell's own 30 and 1001-1270) is a miss.
A cell with fewer than 10 own readings and no extension record is
UNRESOLVED: the row is listed under `unresolved`, counted neither in nor out,
and criteria.py cannot pass D1 on it. Every other row is read on the cell's
own seeds, as before. The report prints, per cell, the readings from the own
seeds, the extension blocks used, the total, the median, the band and the
verdict.

--json FILE writes the same grades as data, one object per arm (what
criteria.py reads): bands per cell (in band, misses, unreadable, absent),
the mechanism certificate per 252 panel, the VIX row per cell, the rise, the
lever and the level rows with their bands. The text output is unchanged.
"""
import argparse, glob, json, pathlib, sys

LEVEL_ROWS = ("index_drift_pct", "fear_gauge_dn1", "fear_gauge_dn3", "index_tail_dn3_pct")


def load(box, sub, days):
    d = box / sub / ("cells" if days == 252 else f"cells{days}")
    return {c["cell"]: c for c in (json.load(open(f)) for f in sorted(glob.glob(str(d / "cell-*.json")))
                                   if not f.endswith(".refused.json"))}


def load_ext(box, sub, days):
    d = box / sub / ("crisisext" if days == 252 else f"crisisext{days}")
    return {c["cell"]: c for c in (json.load(open(f)) for f in sorted(glob.glob(str(d / "ext-*.json"))))}


# The registered numbers (ptv20-registration.md); crisisext_box.py carries the same.
# Nine blocks, 1001-1270: with the cell's own 30, the rule's "300 seeds in all".
CRISIS_ROW, CRISIS_NEED, CRISIS_BLOCK, CRISIS_FIRST, CRISIS_BLOCKS = "crisis_sector_dispersion", 10, 30, 1001, 9


def crisis_grade(cell, ext, band, aggregate):
    """The ten-reading rule for one cell. Returns the row's record: own readings,
    the extension blocks used, the total, the median, the band and the status
    (pass, fail, or unresolved with the reason)."""
    own = [p[CRISIS_ROW] for p in cell["held"] if p.get(CRISIS_ROW) is not None]
    r = {"own_seeds": len(cell["held"]), "own_readings": len(own), "blocks_used": 0, "extension_seeds": None,
         "extension_readings": 0, "total": len(own), "median": None, "band": list(band) if band else None,
         "extended": False, "status": None, "note": None}
    readings = list(own)
    if len(own) >= CRISIS_NEED:
        r["note"] = "not extended: own seeds gave %d" % len(own)
    elif ext is None:
        r["status"], r["note"] = "unresolved", "fewer than %d own readings and no extension record" % CRISIS_NEED
    else:
        rule = ext.get("rule") or {}
        if (rule.get("need"), rule.get("block"), rule.get("first"), rule.get("blocks")) != (
                CRISIS_NEED, CRISIS_BLOCK, CRISIS_FIRST, CRISIS_BLOCKS):
            r["status"], r["note"] = "unresolved", "extension record ran another rule: %s" % rule
        elif ext.get("fingerprint") != cell.get("fingerprint") or ext.get("days") != cell.get("days") \
                or ext.get("own_readings") != len(own):
            r["status"], r["note"] = "unresolved", "extension record is for another cell (fingerprint/days/own readings)"
        else:
            # Block by block, in order, stopping at the first that reaches ten:
            # the grade re-applies the stop rather than trusting the record's.
            ext_read = 0
            for k, b in enumerate(ext.get("blocks") or []):
                lo = CRISIS_FIRST + CRISIS_BLOCK * k
                if b["seeds"] != [lo, lo + CRISIS_BLOCK - 1]:
                    r["status"], r["note"] = "unresolved", "extension block %d is %s, not %d-%d" % (
                        k, b["seeds"], lo, lo + CRISIS_BLOCK - 1)
                    break
                vals = list(b["readings"].values())
                readings += vals; ext_read += len(vals)
                r["blocks_used"] = k + 1
                r["extension_seeds"] = [CRISIS_FIRST, lo + CRISIS_BLOCK - 1]
                if len(readings) >= CRISIS_NEED:
                    break
            r["extended"], r["extension_readings"], r["total"] = r["blocks_used"] > 0, ext_read, len(readings)
            if r["status"] is None and len(readings) < CRISIS_NEED:
                if r["blocks_used"] == CRISIS_BLOCKS:
                    r["status"] = "fail"
                    r["note"] = "fewer than %d readings after %d seeds in all (own %d, %d-%d)" % (
                        CRISIS_NEED, len(cell["held"]) + CRISIS_BLOCK * CRISIS_BLOCKS, len(cell["held"]),
                        CRISIS_FIRST, CRISIS_FIRST + CRISIS_BLOCK * CRISIS_BLOCKS - 1)
                else:
                    r["status"] = "unresolved"
                    r["note"] = "extension stopped after %d of %d blocks with %d readings (%s)" % (
                        r["blocks_used"], CRISIS_BLOCKS, len(readings), ext.get("status"))
    if r["status"] is None:
        r["median"] = aggregate(CRISIS_ROW, readings)
        if band is None:
            r["status"] = "unreadable"
        else:
            r["status"] = "pass" if band[0] <= r["median"] <= band[1] else "fail"
    return r


def crisis_text(r):
    ext = ("blocks %d (%d-%d)" % (r["blocks_used"], *r["extension_seeds"]) if r["blocks_used"] else "blocks 0")
    med = "-" if r["median"] is None else "%.4f" % r["median"]
    band = "-" if r["band"] is None else "[%s, %s]" % tuple(r["band"])
    return ("own %2d of %d, extension %s +%d, total %3d, median %s, band %s -> %s" % (
        r["own_readings"], r["own_seeds"], ext, r["extension_readings"], r["total"], med, band, r["status"].upper()
        if r["status"] in ("fail", "unresolved") else r["status"])) + ("  (%s)" % r["note"] if r["note"] else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("box")
    ap.add_argument("--engine", required=True)
    ap.add_argument("--out")
    ap.add_argument("--json", help="write the grades as data (criteria.py reads this)")
    a = ap.parse_args()
    eng = pathlib.Path(a.engine)
    # The source tree's python/ only where a built _core sits in it (a desk
    # editable install); on a box the wheel is in the venv and the bare tree
    # would shadow it, as longrun.py's _engine says.
    built = any((eng / "python" / "tradefloor").glob("_core*.so"))
    sys.path[:0] = ([str(eng / "python")] if built else []) + [str(eng / "tools" / "calibration")]
    import preset_panel as pp
    from tradefloor import envelope, facts

    box = pathlib.Path(a.box)
    t252, t504, _ = pp._tables(facts.DEFAULT_BAND_BASIS)
    cert, cert504 = load(box, "cert", 252), load(box, "cert", 504)
    hs, hu = load(box, "heldseeds", 252), load(box, "heldu", 252)
    ext = {"panel_252": load_ext(box, "cert", 252), "panel_504": load_ext(box, "cert", 504),
           "heldout_seeds": load_ext(box, "heldseeds", 252), "heldout_universe": load_ext(box, "heldu", 252)}
    assert facts.AGGREGATE.get(CRISIS_ROW, "median") == "median", "the rule grades the median"
    arms = sorted(set(cert) | set(hs) | set(hu), key=lambda x: (x != "SHIPPED", x))
    R = facts.VIX_AR1_ROW
    L = []
    say = lambda s="": (print(s), L.append(s))
    J = {}
    if not any(ext.values()):
        say(f"no crisis extension records under {box} (crisisext_box.py did not run): a cell with fewer than "
            f"{CRISIS_NEED} own readings of {CRISIS_ROW} is unresolved")
        say()

    for arm in arms:
        say(f"=== {arm}")
        G = J[arm] = {"bands": {}, "certify": {}, "vix_row": {}, "rise": None, "lever": None, "level": {},
                      "crisis_dispersion": {}}
        for label, cells, table in (("panel_252", cert, t252), ("panel_504", cert504, t504),
                                    ("heldout_seeds", hs, t252), ("heldout_universe", hu, t252)):
            if arm not in cells:
                say(f"  {label:17s} -"); G["bands"][label] = None; continue
            # Every row on the cell's own seeds, except crisis_sector_dispersion,
            # which the ten-reading rule grades (crisis_grade) and which is taken
            # out here so _count_in_band sees it absent, then put back by verdict.
            panel = pp._median_panel(cells[arm]["held"])
            panel.pop(CRISIS_ROW, None)
            n, miss, unr, ab = pp._count_in_band(panel, table)
            miss, unr, ab = list(miss or []), list(unr or []), list(ab or [])
            cr = G["crisis_dispersion"][label] = crisis_grade(cells[arm], ext[label].get(arm), table.get(CRISIS_ROW),
                                                              facts.aggregate_value)
            unresolved = []
            if CRISIS_ROW in ab:
                ab.remove(CRISIS_ROW)
                if cr["status"] == "pass":
                    n += 1
                elif cr["status"] == "fail":
                    miss.append(CRISIS_ROW)
                else:
                    unresolved.append(CRISIS_ROW)
            G["bands"][label] = {"in_band": n, "misses": miss, "unreadable": unr, "absent": ab,
                                 "unresolved": unresolved}
            say(f"  {label:17s} in band {n:2d}  misses {miss or '-'}"
                + (f"  unreadable {unr}" if unr else "") + (f"  absent {ab}" if ab else "")
                + (f"  unresolved {unresolved}" if unresolved else ""))
            say(f"    {CRISIS_ROW}: " + crisis_text(cr))
        for label, cells in (("panel_252", cert), ("heldout_seeds", hs)):
            if arm in cells:
                c = envelope.certify(cells[arm]["held"])
                ct = c["counts"]
                G["certify"][label] = {"ruled_in_band": ct["in_band_ruled"], "ruled_of": ct["in_band_ruled_of"],
                                       "shown": sorted(c["shown"]), "not_shown": list(c["not_shown"]),
                                       "reversed": list(c["reversed"])}
                say(f"  certify({label}): ruled {ct['in_band_ruled']}/{ct['in_band_ruled_of']}; mechanism shown "
                    f"{len(c['shown'])}, not shown {c['not_shown']}, reversed {c['reversed']}")
        for label, cells, h in (("panel_252", cert, 252), ("panel_504", cert504, 504), ("heldout_seeds", hs, 252)):
            if arm in cells:
                v = facts.structure_verdict([p[R] for p in cells[arm]["held"]], R, horizon_days=h)
                G["vix_row"][label] = {k: v[k] for k in ("median", "real_centre", "k", "n", "cut", "verdict", "side")}
                say(f"  VIX row {label:14s} median {v['median']:.4f} centre {v['real_centre']:.4f} "
                    f"k {v['k']}/{v['n']} cut {v['cut']} -> {v['verdict']}" + (f"/{v['side']}" if v["side"] else ""))
        if arm in cert and arm in cert504:
            r = facts.structure_rise_verdict([p[R] for p in cert[arm]["held"]], [p[R] for p in cert504[arm]["held"]], R)
            G["rise"] = {"median_rise": r["median_rise"], "verdict": r["verdict"], "side": r.get("side")}
            say(f"  VIX rise {r['median_rise']:+.4f} -> {r['verdict']}" + (f"/{r['side']}" if r.get("side") else ""))
        if arm in cert and cert[arm].get("lever_lo"):
            lo = pp._median_panel(cert[arm]["lever_lo"])["annualised_vol_pct"]
            hi = pp._median_panel(cert[arm]["lever_hi"])["annualised_vol_pct"]
            say(f"  crisis lever {hi:.2f} / {lo:.2f} = {hi / lo:.3f}x  (tape 6.16x)")
            G["lever"] = {"hi": hi, "lo": lo, "ratio": hi / lo, "tape": 6.16}
        if arm in cert and cert[arm].get("vary"):
            agg = facts.aggregate_panels(cert[arm]["vary"], LEVEL_ROWS)
            for row in LEVEL_ROWS:
                b, v = t252.get(row), agg.get(row)
                mark = "-" if v is None or b is None else ("in band" if b[0] <= v <= b[1] else "OUT")
                G["level"][row] = {"value": v, "band": list(b) if b else None,
                                   "in_band": None if v is None or b is None else bool(b[0] <= v <= b[1])}
                say(f"  level {row:20s} {'-' if v is None else round(v, 4)}  band {b}  {mark}")
        say()
    if a.out:
        pathlib.Path(a.out).write_text("\n".join(L) + "\n")
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps({"kind": "certgrade_box", "box": str(box), "arms": J,
                                                    "crisis_rule": {"row": CRISIS_ROW, "need": CRISIS_NEED,
                                                                    "block": CRISIS_BLOCK, "first": CRISIS_FIRST,
                                                                    "blocks": CRISIS_BLOCKS}},
                                                   indent=1, default=str) + "\n")


if __name__ == "__main__":
    main()
