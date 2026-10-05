"""cert_lr270.py BOX [ARM ...]: box ptv21c1's long-run rows on 270 histories, as the eighteenth registration's
r13reg/lr270.py reads them.

The owner's decision after grade 17 (2026-10-05): every row read on the long run's 90 histories that can be computed
from 270 is read on 270, with bands and rules unchanged. The 270 are the long run's own 90 (BOX/longrun/ARM, seeds
101-130, 401-430, 701-730, box ptv20g6's) and a pool of 180 (BOX/longrun-pool/ARM) recorded by the same instrument
(longrun.py measure: each seed's free 21-year history and its 2008 and 2020 replays). The pool's seeds are the long
run's three blocks offset by 50000 and by 60000, the offsets seedplan.py's longrunpool protocol puts on its long run.

This is lr270.py's build and prepare with one difference: lr270.py checks each seed set against seedplan.py, whose
rule refuses 101-130, 401-430 and 701-730 always (they were the r13 calibration's exam seeds). Those are the seeds
the certification repeats on purpose, so this checks each set against the exact list this box was launched with.

Writes, beside the box's 90-history files:
    BOX/lr270/longrun/ARM/                 links to the 270 seeds' files, and meta.json naming both sets
    BOX/lr270/longrun-report.txt, .json    longrun.py report over the 270 (A1-A3, B1-B8, C1, C2)
    BOX/lr270/v1.json                      results/ptv20/v1.py over the 270 (V1)
    BOX/lr270/xsec.json                    BOX/xsec.json with B9's annual spread (grade_xsec.b9a) on the 270; its other
                                           rows stay as the xsec stage recorded them
criteria.py then reads --longrun, --v1 and --xsec from BOX/lr270 and everything else from BOX, as grade_all.py does.
Without a full 270 nothing is built and it exits non-zero.
"""
import glob
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROG = os.environ.get("TF_PROGRAMME", os.path.dirname(os.path.dirname(HERE)))
PY = os.environ.get("TF_PY", sys.executable)
SUB = "lr270"
LONGRUN = os.environ.get("CERT_LONGRUN_SEEDS", "101-130,401-430,701-730")
POOL = os.environ.get("CERT_POOL_SEEDS",
                      "50101-50130,50401-50430,50701-50730,60101-60130,60401-60430,60701-60730")


def expand(text):
    out = []
    for part in text.split(","):
        a, _, b = part.strip().partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return sorted(out)


def seed_of(f):
    return int(re.findall(r"(\d+)", os.path.basename(f))[0])


def _seeds(d):
    return sorted({seed_of(f) for f in glob.glob(f"{d}/*-free.npz")})


def build(box, arm):
    base, pool = f"{box}/longrun/{arm}", f"{box}/longrun-pool/{arm}"
    bs, ps = _seeds(base), _seeds(pool)
    want_b, want_p = expand(LONGRUN), expand(POOL)
    if bs != want_b or ps != want_p or len(set(bs) | set(ps)) != len(want_b) + len(want_p):
        return None, (f"not built: {len(bs)} long-run and {len(ps)} pooled histories, not the launched "
                      f"{len(want_b)} ({LONGRUN}) and {len(want_p)} ({POOL})")
    if not (os.path.exists(f"{base}/meta.json") and os.path.exists(f"{pool}/meta.json")):
        return None, "not built: a meta.json is missing"
    mb, mp = json.load(open(f"{base}/meta.json")), json.load(open(f"{pool}/meta.json"))
    for k in ("arm", "base", "dials", "years", "fingerprint"):
        if mb.get(k) != mp.get(k):
            return None, f"not built: the pool's {k} is not the long run's"
    if (mb.get("build") or {}).get("commit") != (mp.get("build") or {}).get("commit"):
        return None, "not built: the pool ran on another engine commit"
    dest = f"{box}/{SUB}/longrun/{arm}"
    os.makedirs(dest, exist_ok=True)
    for f in os.listdir(dest):
        os.remove(os.path.join(dest, f))
    json.dump(dict(mb, lr270={"longrun": LONGRUN, "pool": POOL}), open(f"{dest}/meta.json", "w"), indent=1)
    for src, seeds in ((base, bs), (pool, ps)):
        for s in seeds:
            for part in ("free", "gfc", "covid"):
                for ext in ("json", "npz"):
                    f = f"{src}/{s}-{part}.{ext}"
                    if not os.path.exists(f):
                        return None, f"not built: {f} is missing"
                    os.symlink(os.path.abspath(f), f"{dest}/{s}-{part}.{ext}")
    return dest, f"{len(bs) + len(ps)} histories: longrun {LONGRUN} + pool {POOL}"


def prepare(box, arms=None):
    box = os.path.abspath(box)
    arms = arms or sorted(os.path.basename(p) for p in glob.glob(f"{box}/longrun/*") if os.path.isdir(p))
    notes, built = {}, []
    for arm in arms:
        d, note = build(box, arm)
        notes[arm] = note
        if d:
            built.append(arm)
    if len(built) != len(arms):
        raise SystemExit("cert_lr270: " + json.dumps(notes, indent=1))
    root = f"{box}/{SUB}"
    lr = f"{PROG}/longrun"
    r = subprocess.run([PY, f"{lr}/longrun.py", "report", f"{root}/longrun", "--out", f"{root}/longrun-report.txt"],
                       cwd=lr, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("cert_lr270: longrun.py report failed: " + r.stderr[-2000:])
    r = subprocess.run([PY, f"{PROG}/results/ptv20/v1.py", root, "--out", f"{root}/v1.json"], cwd=lr,
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("cert_lr270: v1.py failed: " + r.stderr[-2000:])
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
