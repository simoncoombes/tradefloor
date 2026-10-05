"""lr270.py BOX [ARM ...]: the long run on all 270 of the grade's long-run histories (the eighteenth registration).

The owner's decision after grade 17 (2026-10-05): every row read on the long run's 90 histories that can be computed
from all 270 of the grade's long-run histories is read on all 270. Bands and rules stay the same; only the sample
grows. The 270 are the long run's own 90 (longrun/ARM, protocol longrun) and the pool's 180 (longrun-pool/ARM,
protocol longrunpool), which the pool stage records with the same instrument (longrun.py measure: each seed's free
21-year history and its 2008 and 2020 replays).

build(box, arm) links both sets' files into BOX/lr270/longrun/ARM after the checks grade_all.load_pool makes (each
seed set passes seedplan.py's rule; exactly 90 + 180 distinct seeds; the pool's dials are the long run's). Then, for
the whole box, `prepare(box)` writes beside them what the box wrote for the 90:

    BOX/lr270/longrun-report.txt, .json   longrun.py report over the 270 (A1-A3, B1-B8, C1, C2)
    BOX/lr270/v1.json                     results/ptv20/v1.py over the 270 (V1)
    BOX/lr270/xsec.json                   BOX/xsec.json with B9's annual spread (grade_xsec.b9a) read on the 270;
                                          its other rows (B9's start-up ratio, C5-C8, E1, D2, F1, L1) are the
                                          xsec stage's own runs and stay as recorded

grade_all.py passes these to criteria.py and reads its long-run proposed rows on the 270; exploits.py points the
index-level screens, rule_cut and the levered rules at BOX/lr270/longrun/ARM. Without a full 270 nothing is built and
the rows that need it are not graded (a fail).
"""
import glob
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "box"))
import seedplan  # noqa: E402

PROG = os.environ.get("TF_PROGRAMME", os.path.dirname(HERE))
PY = os.environ.get("TF_PY", sys.executable)
NEED = 270
SUB = "lr270"


def seed_of(f):
    return int(re.findall(r"(\d+)", os.path.basename(f))[0])


def _seeds(d):
    return sorted({seed_of(f) for f in glob.glob(f"{d}/*-free.npz")})


def build(box, arm):
    """(directory, note): BOX/lr270/longrun/ARM holding every file of the 270 seeds, or (None, why)."""
    base, pool = f"{box}/longrun/{arm}", f"{box}/longrun-pool/{arm}"
    bs, ps = _seeds(base), _seeds(pool)
    seedplan.guard(bs, "longrun")
    seedplan.guard(ps, "longrunpool")
    if len(bs) != 90 or len(ps) != NEED - 90 or len(set(bs) | set(ps)) != NEED:
        return None, (f"not built: {len(bs)} long-run and {len(ps)} pooled histories ({len(set(bs) | set(ps))} "
                      f"distinct seeds; the 270 reads need 90 and 180)")
    if not (os.path.exists(f"{base}/meta.json") and os.path.exists(f"{pool}/meta.json")):
        return None, "not built: a meta.json is missing"
    mb, mp = json.load(open(f"{base}/meta.json")), json.load(open(f"{pool}/meta.json"))
    for k in ("arm", "base", "dials", "years"):
        if mb.get(k) != mp.get(k):
            return None, f"not built: the pool's {k} is not the long run's"
    dest = f"{box}/{SUB}/longrun/{arm}"
    os.makedirs(dest, exist_ok=True)
    for f in os.listdir(dest):
        os.remove(os.path.join(dest, f))
    json.dump(dict(mb, lr270={"longrun": seedplan.fmt(bs), "pool": seedplan.fmt(ps)}), open(f"{dest}/meta.json", "w"),
              indent=1)
    for src, seeds in ((base, bs), (pool, ps)):
        for s in seeds:
            for part in ("free", "gfc", "covid"):
                for ext in ("json", "npz"):
                    f = f"{src}/{s}-{part}.{ext}"
                    if not os.path.exists(f):
                        return None, f"not built: {f} is missing"
                    os.symlink(os.path.abspath(f), f"{dest}/{s}-{part}.{ext}")
    return dest, f"{NEED} histories: longrun {seedplan.fmt(bs)} + pool {seedplan.fmt(ps)}"


def prepare(box, arms=None):
    """Build every arm's 270 and write the report, V1 and xsec files the criteria read. Returns {arm: note}."""
    box = os.path.abspath(box)
    arms = arms or sorted(os.path.basename(p) for p in glob.glob(f"{box}/longrun/*") if os.path.isdir(p))
    notes, built = {}, []
    for arm in arms:
        d, note = build(box, arm)
        notes[arm] = note
        if d:
            built.append(arm)
    root = f"{box}/{SUB}"
    if not built:
        return notes
    lr = f"{PROG}/longrun"
    r = subprocess.run([PY, f"{lr}/longrun.py", "report", f"{root}/longrun", "--out", f"{root}/longrun-report.txt"],
                       cwd=lr, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("lr270: longrun.py report failed: " + r.stderr[-2000:])
    subprocess.run([PY, f"{PROG}/results/ptv20/v1.py", root, "--out", f"{root}/v1.json"], cwd=lr,
                   capture_output=True)
    if os.path.exists(f"{box}/xsec.json"):
        sys.path.insert(0, f"{PROG}/results/ptv20")
        import grade_xsec as GX
        x = json.load(open(f"{box}/xsec.json"))
        for arm in built:
            a = x.get("arms", {}).get(arm)
            if a is None:
                continue
            a["B9_annual_sd_pct_90"], a["B9_annual_n_90"] = a.get("B9_annual_sd_pct"), a.get("B9_annual_n")
            a["B9_annual_sd_pct"], a["B9_annual_n"] = GX.b9a(f"{root}/longrun", arm)
            a["B9_annual_from"] = notes[arm]
        json.dump(x, open(f"{root}/xsec.json", "w"), indent=1)
    json.dump(notes, open(f"{root}/notes.json", "w"), indent=1)
    return notes


if __name__ == "__main__":
    for arm, note in prepare(sys.argv[1], sys.argv[2:] or None).items():
        print(arm, note)
