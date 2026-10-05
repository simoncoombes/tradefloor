"""cert_reg18_box.py -- the two readings the eighteenth registration's R4 and D1 need, by name, on the certification
box's seeds (box ptv21c1s, supplementing ptv21c1). Runs on the box from scripts/longrun/.

    python cert_reg18_box.py gen    OUTDIR ARMS_FILE SEEDS DAYS WORKERS     true-phase histories (r14gen.py)
    python cert_reg18_box.py d1pool OUTDIR ARMS_FILE SEEDS [--workers N]   the pooled level rows (d1pool_box.py)

The measuring code is the eighteenth registration's own, r14gen.py and d1pool_box.py, unchanged in what they
measure. Two things differ, both because this is the twelfth registration's certification protocol (box ptv20g6's
seeds) rather than a registered grade:

  - seeds: seedplan.py refuses the old exam seeds (101-190, 401-430, 701-730) always, and this protocol runs on
    them by design (g6's long run is 101-130, 401-430, 701-730). Each seed list is checked here against the list
    the box was launched with (GEN_SEEDS, D1POOL_SEEDS in its environment) instead of against seedplan.py's rule.
  - the preset by name: r14gen.py builds pt-v20 with each arm's dials. Here the arm line names its base
    (pt-v21@pt-v21:), and the histories are built from that preset by name with no dials. d1pool_box.py already
    reads the base from the arm line.

For gen, the copy of r14gen.py this runs is written beside OUTDIR with exactly those replacements (each asserted to
occur once), so what ran is on the box's record (OUTDIR/../r14gen-byname.py).
"""
import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent


def arm_lines(path):
    out = []
    for line in open(path):
        line = line.split("#")[0].strip()
        if line:
            head, _, body = line.partition(":")
            name, _, base = head.partition("@")
            out.append((name.strip(), (base or "pt-v20").strip(), body.strip()))
    return out


def expect(var, given):
    want = os.environ.get(var)
    if want != given:
        sys.exit(f"REFUSED: {var} is {want!r} in the box's environment, the command gave {given!r}")


def gen(out, arms_file, seeds, days, workers):
    expect("GEN_SEEDS", seeds)
    arms = arm_lines(arms_file)
    if len(arms) != 1 or arms[0][2]:
        sys.exit(f"REFUSED: gen takes one arm by name with no dials, got {arms}")
    name, base, _ = arms[0]
    src = (HERE / "r14gen.py").read_text()
    reps = [
        ('    m = tf.ModelParams.from_preset("pt-v20", **dials)\n',
         f'    m = tf.ModelParams.from_preset({base!r}, **dials)\n'),
        ('preset=np.array("pt-v20")', f'preset=np.array({base!r})'),
        ('    return seedplan.guard(seedplan.parse(text), os.environ.get("SEED_PROTOCOL", "truephase"))\n',
         '    return seedplan.parse(text)          # checked against the launched GEN_SEEDS by cert_reg18_box.py\n'),
    ]
    for a, b in reps:
        if src.count(a) != 1:
            sys.exit(f"REFUSED: r14gen.py does not hold {a.strip()!r} exactly once")
        src = src.replace(a, b)
    os.makedirs(out, exist_ok=True)
    dst = pathlib.Path(out).parent / "r14gen-byname.py"
    dst.write_text(src)
    env = dict(os.environ, PYTHONPATH=str(HERE) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    return subprocess.call([sys.executable, str(dst), out, arms_file, seeds, str(days), str(workers)], env=env)


def d1pool(out, arms_file, seeds, workers):
    expect("D1POOL_SEEDS", seeds)
    sys.path.insert(0, str(HERE))
    import seedplan
    import d1pool_box
    launched = seedplan.parse(seeds)

    def guard(s, protocol, **kw):
        s = list(s)
        if protocol != "d1pool" or s != launched:
            raise SystemExit(f"REFUSED: d1pool seeds {seedplan.fmt(s)} are not the launched {seeds}")
        return s
    seedplan.guard = guard
    d1pool_box.seedplan.guard = guard
    return d1pool_box.main(["d1pool_box.py", out, arms_file, "--workers", str(workers)])


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "gen":
        sys.exit(gen(sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]), int(sys.argv[6])))
    if cmd == "d1pool":
        w = int(sys.argv[sys.argv.index("--workers") + 1]) if "--workers" in sys.argv else 90
        sys.exit(d1pool(sys.argv[2], sys.argv[3], sys.argv[4], w))
    sys.exit(__doc__)
