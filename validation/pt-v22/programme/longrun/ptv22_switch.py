"""ptv22_switch.py -- the pt-v22 products that are in the grade job but switched off (stages.json "ptv22idx",
"ptv22vix"; draft item 11: index options and VIX futures stay off in v22).

    python ptv22_switch.py check idx|vix ARMS_FILE [--json OUT]
        the switched-off stage's own script, on the box: refuses (exit 2) unless the arm lists the product
        (stages.json "requires_dials"); the stage only runs once it is switched on
    python ptv22_switch.py rows [--stages stages.json] [OUT_DIR ARM]
        on the desk: the pt-v22 rows each family grades now, and, with a merged box folder, whether the parts
        that measure a switched-on family recorded its rows

Index options and VIX futures are measured by the parts that run anyway (ptv22fut, ptv22chain, ptv22names,
ptv22traded) whenever the arm lists them, so switching one on is "enabled": true in stages.json and the listing
dial on the arm. Standard library only.
"""
import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
FAMILY = {"idx": "ptv22idx", "vix": "ptv22vix"}
#: The rows each family adds to the grade when it is on, and the part files that carry them.
EVIDENCE = {"idx": ("ptv22chain/{ARM}/chain-*.json", "ptv22names/{ARM}/names-*.json"),
            "vix": ("ptv22fut/{ARM}/pieces-*.json", "ptv22mp/{ARM}/push-*.json")}


def stages_file(path=None):
    for p in ([path] if path else []) + [HERE / "stages.json", HERE.parents[0] / "screen" / "stages.json",
                                          HERE.parents[1] / "screen" / "stages.json"]:
        if p and pathlib.Path(p).exists():
            return json.loads(pathlib.Path(p).read_text())
    raise SystemExit("ptv22_switch: no stages.json")


def arm_dials(line):
    """'NAME@BASE:d=v,...' -> (NAME, {d: v})."""
    head, _, body = line.strip().partition(":")
    dials = {}
    for kv in body.split(","):
        if "=" in kv:
            k, v = kv.split("=", 1)
            dials[k.strip()] = float(v)
    return head.split("@")[0], dials


def first_arm(path):
    for ln in pathlib.Path(path).read_text().splitlines():
        ln = ln.split("#", 1)[0].strip()
        if ln:
            return ln
    raise SystemExit(f"ptv22_switch: no arm in {path}")


def check(fam, arms_file, out=None, stages=None):
    st = stages_file(stages)["stages"][FAMILY[fam]]
    name, dials = arm_dials(first_arm(arms_file))
    need = st.get("requires_dials", {})
    missing = {k: v for k, v in need.items() if dials.get(k) != v}
    rec = {"stage": FAMILY[fam], "enabled": st.get("enabled", True), "arm": name, "requires_dials": need,
           "arm_sets": {k: dials.get(k) for k in need}, "ok": not missing}
    if out:
        pathlib.Path(out).write_text(json.dumps(rec, indent=1) + "\n")
    if missing:
        print(f"REFUSED: {FAMILY[fam]} needs the arm to list it; arm {name} does not: needs {missing}, "
              f"sets {rec['arm_sets']}")
        return 2
    print(f"{FAMILY[fam]}: arm {name} lists it ({need})")
    return 0


def rows(stages=None, out_dir=None, arm=None):
    d = stages_file(stages)["stages"]
    graded, off = [], []
    for k, st in d.items():
        if not k.startswith("ptv22"):
            continue
        (graded if st.get("enabled", True) else off).append(k)
    res = {"graded": {k: d[k].get("rows", []) for k in graded},
           "off": {k: d[k].get("rows", []) for k in off}, "evidence": {}}
    if out_dir and arm:
        for fam, k in FAMILY.items():
            if k in graded:
                res["evidence"][k] = {g: len(list(pathlib.Path(out_dir).glob(g.replace("{ARM}", arm))))
                                      for g in EVIDENCE[fam]}
    print(json.dumps(res, indent=1))
    bad = [k for k, ev in res["evidence"].items() if not all(ev.values())]
    if bad:
        print(f"MISSING: {bad} are switched on and their parts recorded nothing", file=sys.stderr)
        return 2
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check"); c.add_argument("family", choices=sorted(FAMILY)); c.add_argument("arms_file")
    c.add_argument("--json"); c.add_argument("--stages")
    r = sub.add_parser("rows"); r.add_argument("out_dir", nargs="?"); r.add_argument("arm", nargs="?")
    r.add_argument("--stages")
    a = ap.parse_args(argv)
    if a.cmd == "check":
        return check(a.family, a.arms_file, a.json, a.stages)
    return rows(a.stages, a.out_dir, a.arm)


if __name__ == "__main__":
    sys.exit(main())
