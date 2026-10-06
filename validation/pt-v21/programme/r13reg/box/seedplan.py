"""seedplan.py -- every seed block the grade job runs, per protocol, from the variables the thirteenth
registration names, and the rule that says which seed sets may run.

    python seedplan.py show [--json FILE]     per protocol, the seeds the job would run (the job's DRY_RUN=1),
                                              then the verdict of `verify`
    python seedplan.py verify [--json FILE]   exit 2 (REFUSED) unless the plan passes the rule below; --json
                                              writes the plan, REGISTERED_GRADE and grade-seeds.json's sha256

Each protocol's seeds come from one environment variable. A list variable holds a seed list ("201-230,501-530",
"2531,2532,2534"); a first-seed variable holds the first seed of a fixed count. Unset, each variable is today's
held-out value, so a screen launched without them runs what it ran before. r15-grade-jobs.sh and r14-box-jobs.sh
carry the same defaults (tests/test_seedplan.py holds the two to each other).

The rule (the registered-grade rule). A seed set may run, or be graded, when it is
  - held out: none of its seeds is an old exam seed (OLD_EXAM) or a registered exam seed (grade-seeds.json), or
  - exactly the registered exam seed set for its protocol, as grade-seeds.json lists it, with REGISTERED_GRADE set
    to the registration commit. On a checkout that has git, that commit must exist, be on a remote branch
    (pushed), and hold this grade-seeds.json byte for byte. On the box (no design-repo git) the sha is recorded
    in $OUT/seedplan.json with the file's sha256 and grade_all.py checks the two against git on the desk.
Anything else is refused. The old exam seeds, 101-190, 401-430 and 701-730 (run during the r13 calibration), are
refused always, with or without REGISTERED_GRADE.

grade-seeds.json (r13reg/grade-seeds.json on the desk; shipped beside this file on the box). Either
    {"protocols": {"<protocol>": "<seed list>" | [seeds] | {"seeds": "<seed list>" | [seeds]}, ...}, ...}
or the same mapping keyed by variable name (top level, or under "variables"). A first-seed protocol may give its
first seed alone or its full list. GRADE_SEEDS_FILE overrides where the file is looked for.
Standard library only: it runs on the desk's system python and on the box before anything is built."""
import hashlib
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OLD_EXAM = ((101, 190), (401, 430), (701, 730))

# (protocol, variable, default = today's held-out value, count (first-seed variable) or None (list), what runs it)
PROTOCOLS = [
    ("longrun", "LONGRUN_SEED_LIST", "201-230,501-530,801-830", None,
     "long run 21y + gfc/covid replays (longrun-jobs.sh): A, B1-B9, C1, C2, C10, E1, L1, V1, ..., rule_cut, index exploit screens"),
    # owner's decision after grade 17 (2026-10-05, eighteenth registration): the noisy rows on three times the seeds;
    # the true-phase histories on three blocks of 90 (B10-B12, DV, G, R4m, F-bear and the other bt: rows)
    ("truephase", "GEN_SEEDS", "201-290,501-590,801-890", None,
     "true-phase histories, 5292 sessions (r14gen.py), 270 since the eighteenth registration: B10-B12, DV, G, C10e, "
     "VC1, VC2, T1-T3, I-rate, R3 held, R3m, R4m, R4-lag, F-stress, F-bear"),
    ("leakmacro", "EXPLOITS_LEAK_FIRST", "201", 24,
     "leak macro rules, read from truephase (exploits.py, desk): rule_peak, rule_cut, rule_unemp_floor, ..."),
    ("cert", "CERT_SEEDS", "201-230", None, "certification panel_252 (with the lever) and panel_504: D1, VC3"),
    ("heldseeds", "HELDSEEDS_SEEDS", "501-530", None, "certification heldout_seeds: D1"),
    ("heldu", "HELDU_SEEDS", "801-830", None, "certification heldout_universe (60 names, roster 909): D1"),
    ("crisisext", "CRISISEXT_FIRST", "2001", 270,
     "D1 crisis-dispersion extension, blocks of 30, nine at most (crisisext_box_hr.py; certgrade_box_hr.py)"),
    ("edge", "EDGE_FIRST_SEED", "201", 10, "edge.py, 10 seeds x 300 days: C3"),
    ("c4a", "C4A_FIRST_SEED", "201", 8, "c4.py part a, 8 seeds: C4a"),
    ("xsec", "XSEC_SEEDS", "201-230,501-530,801-830", None,
     "grade_xsec.py, driven2020.py, driven2022.py: C5-C8, D2, F1, R5, R6"),
    # owner decision 13 (2026-09-29): R7a and its last-close baseline on the three held-out blocks (90 seeds);
    # R7b keeps its 30 histories (its bound is 20 of 30), on R7EVAL_SEEDS
    ("r7", "R7_SEEDS", "201-230,501-530,801-830", None, "r7_event.py: R7a (90 seeds, owner decision 13)"),
    ("r7pre", "R7PRE_SEEDS", "201-230,501-530,801-830", None,
     "r7_event_pre.py: R7a with the last-close baseline (90 seeds, owner decision 13)"),
    ("r7eval", "R7EVAL_SEEDS", "201-230", None, "r7_eval.py: R7b (30 histories, its 20-of-30 bound)"),
    ("recession", "REC_SEEDS", "201-290", None, "recession_rows.py: S1a, S1b, S2 (90 seeds since the eighteenth registration)"),
    ("sfrec", "SF_REC_SEEDS", "201-230", None, "desk_sf.py, packaged recession: SF1-SF5"),
    ("sf", "SF_SEEDS", "201-236", None, "desk_sf.py, the other six files: SF2, SF3, SF5 (36 seeds since the eighteenth registration)"),
    ("mcrun", "MC_RUN_SEEDS", "2501-2530", None, "metaorder_curve.py run: Q1-Q9"),
    ("mctrips", "MC_TRIP_SEEDS", "2531,2532,2534", None, "metaorder_curve.py trips: G-rt"),
    ("mcmark", "MC_MARK_SEED", "2533", None, "metaorder_curve.py mark: reported"),
    ("ao", "AO_FIRST_SEED", "3001", 30, "e7_arm.py, 30 seeds: AO1-AO4"),
    ("ph1", "PH1_SEEDS", "2001-2005", None, "ph_rows.py ph1: PH1"),
    ("ph3", "PH3_SEEDS", "2001-2030", None, "ph_rows.py ph3: PH3"),
    ("edgegen", "EDGEGEN_SEEDS", "2001-2030", None, "edgegen_arm.py gen: stock_screen, remark rules"),
    ("edgeeval", "EDGEEVAL_SEEDS", "3001-3020", None,
     "edgegen_arm.py eval and evalem: fedcut63, pubpeakcon, fg40, unemp126 and their exposure-matched runs"),
    ("regr_short", "AUDIT_SEED_SHORT", "2101", 30, "regr.tgz gen_all/strategies short, intraday"),
    ("regr_var", "AUDIT_SEED_VAR", "2201", 30, "regr.tgz gen_all/strategies var"),
    ("regr_long", "AUDIT_SEED_LONG", "2301", 8, "regr.tgz gen_all/strategies long, stale_open, big_days"),
    ("regr_probe", "AUDIT_SEED_PROBE", "2401", 8,
     "regr.tgz probe_impact (3), run_mm_thin (8), probe_pins_paired (3), from the same first seed"),
    ("stab", "STAB_FIRST_SEED", "201", 12, "stab60.py, 12 x 100 years: H1-100y"),
    ("probe", "PROBE_SEEDS", "201-236", None, "probe_all.py: L-rate (36 seeds since the eighteenth registration)"),
    # owner decisions 10 and 11 (2026-09-28), from the fourteenth registration
    ("d1pool", "D1POOL_SEEDS", "201-400,431-590", None,
     "d1pool_box.py, the certification's varying-roster protocol, 360 seeds: D1's tail row as a pooled rate"),
    ("aopool", "AOPOOL_FIRST", "43001", 400,
     "e7_arm.py q2/q3 and ao_coin.py, 400 seeds: AO1 (the book coin), AO3 and AO4 (luck removed)"),
    # owner decisions 14 and 15 (2026-09-29): C10c and PH5 on 270 long-run histories, the long run's own 90
    # (LONGRUN_SEED_LIST) and two more 90-history blocks here
    ("longrunpool", "LONGRUN_POOL_SEED_LIST",
     "50201-50230,50501-50530,50801-50830,60201-60230,60501-60530,60801-60830", None,
     "longrun.py measure into longrun-pool/ (21y free histories and replays): with the long run's 90, the 270 "
     "histories C10c and PH5 are graded on"),
    # the sixteenth registration (pt-v21): the rows CRITERIA-pt-v21.md registered (ON1, U1-U4, O1-O4, SK1, SK2),
    # measured by the screen's ptv21 stage (ptv21.py measure --parts free, 21-year histories)
    ("ptv21", "PTV21_SEEDS", "201-290", None,
     "ptv21.py measure --parts free (the screen's ptv21 stage), 90 x 21 years since the eighteenth registration: ON1, "
     "U1-U4, O1-O4, SK1, SK2"),
]
BY_NAME = {p[0]: p for p in PROTOCOLS}
# seeds that do not move with the grade (printed by `show`, not variables)
FIXED = [
    ("c4b", "92001-92020", "c4.py part b, the published suite (universes 93001-93020): C4b"),
    ("c9", "impact_curve's own", "impact_curve.py with the arm's dials: C9"),
    ("scenario_size", "301-330", "scenario_size.py; skipped under GRID=1 (not a graded row)"),
    ("regr_fixed", "17, 7-17", "probe_bond_stale seed 17, probe_invariants seeds 7-17: construction checks"),
]


class Refused(SystemExit):
    pass


def parse(spec):
    """'201-230,501-530,2531' -> [201, ..., 230, 501, ..., 530, 2531]."""
    out = []
    for part in str(spec).replace(" ", "").split(","):
        if not part:
            continue
        lo, _, hi = part.partition("-")
        lo, hi = int(lo), int(hi or lo)
        if hi < lo:
            raise ValueError(f"seed range {part} runs backwards")
        out += range(lo, hi + 1)
    return out


def fmt(seeds):
    """[201, ..., 230, 2531] -> '201-230,2531' (order kept)."""
    out, run = [], None
    for s in seeds:
        if run and s == run[1] + 1:
            run[1] = s
        else:
            if run:
                out.append(run)
            run = [s, s]
    if run:
        out.append(run)
    return ",".join(f"{a}" if a == b else f"{a}-{b}" for a, b in out)


def expand(value, count):
    """A variable's value -> its seed list. A first-seed variable takes `count` seeds from its value; given a
    list or range instead (grade-seeds.json may), that list is taken as it stands."""
    if isinstance(value, (list, tuple)):
        return [int(s) for s in value]
    if isinstance(value, int):
        value = str(value)
    if count and "-" not in str(value) and "," not in str(value):
        first = int(value)
        return list(range(first, first + count))
    return parse(value)


def plan(env=None):
    """{protocol: [seeds]} as the job would run it under `env` (default os.environ)."""
    env = os.environ if env is None else env
    return {p: expand(env.get(var) or default, n) for p, var, default, n, _ in PROTOCOLS}


def old_exam(seeds):
    return sorted(s for s in set(seeds) if any(a <= s <= b for a, b in OLD_EXAM))


def grade_seeds_path(env=None):
    env = os.environ if env is None else env
    if env.get("GRADE_SEEDS_FILE"):
        if not os.path.exists(env["GRADE_SEEDS_FILE"]):
            raise Refused(f"REFUSED: GRADE_SEEDS_FILE {env['GRADE_SEEDS_FILE']} does not exist")
        return env["GRADE_SEEDS_FILE"]
    for p in (os.path.join(HERE, "grade-seeds.json"), os.path.join(os.path.dirname(HERE), "grade-seeds.json")):
        if os.path.exists(p):
            return p
    return None


def load_registered(path):
    """grade-seeds.json -> ({protocol: [seeds]} for every protocol it lists, [protocols it does not list])."""
    d = json.load(open(path))
    srcs = [d.get("protocols") or {}, d.get("variables") or {}, d]
    reg, missing = {}, []
    for p, var, _, n, _ in PROTOCOLS:
        v = next((s[k] for s in srcs for k in (p, var) if isinstance(s, dict) and k in s), None)
        if isinstance(v, dict):
            v = v.get("seeds", v.get("value", v.get("first")))
        if v is None:
            missing.append(p)
            continue
        reg[p] = expand(v, n)
    return reg, missing


def sha256(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _git(cwd, *args):
    try:
        r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r


def check_registered_commit(sha, path):
    """None when REGISTERED_GRADE=sha may unlock the exam seeds in `path`, else the reason it may not."""
    if not sha or not all(c in "0123456789abcdef" for c in sha.lower()) or len(sha) < 7:
        return f"REGISTERED_GRADE {sha!r} is not a commit sha"
    d = os.path.dirname(os.path.abspath(path))
    top = _git(d, "rev-parse", "--show-toplevel")
    if top is None or top.returncode != 0:
        return None          # the box: no design-repo git; the sha and the file's sha256 go into seedplan.json
    top = top.stdout.decode().strip()
    rel = os.path.relpath(os.path.realpath(path), os.path.realpath(top))
    c = _git(d, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}")
    if c.returncode != 0:
        return f"REGISTERED_GRADE {sha} is not a commit of this repo"
    remote = _git(d, "branch", "-r", "--contains", sha)
    if remote.returncode != 0 or not remote.stdout.strip():
        return f"REGISTERED_GRADE {sha} is on no remote branch (the registration commit is pushed before any exam seed runs)"
    at = _git(d, "show", f"{sha}:{rel}")
    if at.returncode != 0:
        return f"{rel} is not in commit {sha}"
    if at.stdout != open(path, "rb").read():
        return f"{rel} differs from the one registered in {sha}"
    return None


def verdict(seeds_by_protocol, env=None, path=None, exact=True):
    """[] when every protocol's seed set may run under the rule, else the reasons it may not. With exact=False a
    registered protocol may be a subset of its registered set (a partial read, as the D1 extension's blocks)."""
    env = os.environ if env is None else env
    path = path or grade_seeds_path(env)
    sha = env.get("REGISTERED_GRADE", "").strip()
    why = []
    reg, missing = ({}, []) if not path else load_registered(path)
    for p, r in reg.items():
        if old_exam(r):
            why.append(f"grade-seeds.json registers old exam seeds for {p}: {fmt(old_exam(r))}")
    exam = set().union(*reg.values()) if reg else set()
    unlocked = None
    for p, seeds in seeds_by_protocol.items():
        s = set(seeds)
        if old_exam(s):
            why.append(f"{p}: old exam seeds {fmt(old_exam(s))} (101-190, 401-430, 701-730 are refused always)")
            continue
        if not s & exam:
            if sha and exact:
                why.append(f"{p}: {fmt(sorted(s))} is held out, but REGISTERED_GRADE is set: the grade runs "
                           f"the registered set {fmt(reg.get(p, [])) or '(none listed)'}")
            continue
        if not sha:
            why.append(f"{p}: {fmt(sorted(s & exam))} are registered exam seeds and REGISTERED_GRADE is not set")
            continue
        if unlocked is None:
            unlocked = check_registered_commit(sha, path)
        if unlocked:
            why.append(f"{p}: {unlocked}")
            continue
        if p not in reg:
            why.append(f"{p}: grade-seeds.json lists no seeds for this protocol")
        elif (s != set(reg[p])) if exact else not s <= set(reg[p]):
            why.append(f"{p}: {fmt(sorted(s))} is not exactly the registered {fmt(reg[p])}")
    if sha and exact:
        if not path:
            why.append("REGISTERED_GRADE is set and there is no grade-seeds.json")
        for p in missing:
            if p in seeds_by_protocol:
                why.append(f"{p}: grade-seeds.json lists no seeds for this protocol")
    return why


def guard(seeds, protocol, env=None, path=None, exact=True):
    """Raise Refused unless `seeds` may run (or be graded) as `protocol` under the rule."""
    why = verdict({protocol: list(seeds)}, env=env, path=path, exact=exact)
    if why:
        raise Refused("REFUSED: " + "; ".join(why))
    return list(seeds)


def record(env=None):
    env = os.environ if env is None else env
    path = grade_seeds_path(env)
    return {"plan": {p: fmt(s) for p, s in plan(env).items()},
            "variables": {var: env.get(var) or default for _, var, default, _, _ in PROTOCOLS},
            "registered_grade": env.get("REGISTERED_GRADE") or None,
            "grade_seeds_file": path, "grade_seeds_sha256": sha256(path) if path else None}


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "show"
    js = argv[argv.index("--json") + 1] if "--json" in argv else None
    pl = plan()
    if cmd == "show":
        print(f"{'protocol':11s} {'variable':20s} {'n':>4s}  seeds")
        for p, var, _, _, what in PROTOCOLS:
            print(f"{p:11s} {var:20s} {len(pl[p]):4d}  {fmt(pl[p])}")
        for p, s, what in FIXED:
            print(f"{p:11s} {'(fixed)':20s} {'':4s}  {s}  -- {what}")
    elif cmd != "verify":
        raise SystemExit(__doc__)
    why = verdict(pl)
    rec = record()
    if js:
        json.dump({**rec, "verdict": why or "ok"}, open(js, "w"), indent=1)
    gs = rec["grade_seeds_file"]
    print(f"seedplan: REGISTERED_GRADE={rec['registered_grade'] or '-'} grade-seeds.json="
          f"{(gs + ' sha256 ' + rec['grade_seeds_sha256'][:16]) if gs else '(none)'}")
    if why:
        print("REFUSED:\n  " + "\n  ".join(why))
        return 2
    print("seedplan: ok (" + ("the registered exam set" if rec["registered_grade"] else "held out throughout") + ")")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
